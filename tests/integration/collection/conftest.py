import os
from datetime import UTC, datetime
from threading import get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.collection import EventCollector, EventStore
from app.config import ConnectionSettings, Settings
from app.contracts.dto import EventPayload
from app.contracts.interfaces import (
    AdapterError,
    EventBatch,
    FolderConfig,
    FolderRuntime,
    SourceCheckpoint,
    SourceEvent,
    StreamAnchor,
    SystemObservation,
)
from app.db import Database
from app.models import Base, Folder, ManagedNode
from app.registry.resolver import NodeResolver
from sqlalchemy import event, text
from sqlalchemy.engine import make_url

RAW = "2026-10-25T12:00:00.123456789Z"


class Clock:
    current = None

    def now_utc(self):
        return self.current if self.current is not None else datetime.now(UTC)


def source(position, *, folder="shared", raw=RAW, state="idle"):
    return SourceEvent(
        id=position,
        global_id=10**12 + position,
        type="StateChanged",
        time_raw=raw,
        syncthing_folder_id=folder,
        payload_summary=EventPayload(state=state),
    )


def checkpoint(node, *, epoch="a", raw=RAW, anchor=1):
    return SourceCheckpoint(
        node_id=node,
        start_time_raw=raw,
        source_session_id=epoch * 64,
        anchor=StreamAnchor.from_event(source(anchor, raw=raw)),
    )


def packet(node, *positions, epoch="a", raw=RAW, anchor=None, events=None):
    rows = tuple(source(p, raw=raw) for p in positions) if events is None else tuple(events)
    tail = rows[-1] if rows else source(anchor or 1, raw=raw)
    cp = checkpoint(node, epoch=epoch, raw=raw).model_copy(
        update={"anchor": StreamAnchor.from_event(tail)}
    )
    return EventBatch(
        source_session_id=cp.source_session_id,
        events=rows,
        source_checkpoint=cp,
        observed_at=datetime.now(UTC),
    )


class Factory:
    def __init__(self, realm):
        self.realm, self.closed = realm, False
        self.targets = []

    def for_node(self, node):
        target = NodeResolver(self.realm.settings, self.realm.database)(node)
        self.targets.append(target)
        outer = self

        class Client:
            def read_events(self, since, timeout_seconds):
                assert outer.realm.leases.get(get_ident(), 0) == 0
                outer.realm.calls.append((node, since, timeout_seconds))
                hook = outer.realm.on_read
                if hook:
                    hook()
                if outer.realm.failure:
                    raise AdapterError(outer.realm.failure)
                batch = outer.realm.batch
                if since and (
                    target.checkpoint is None
                    or target.checkpoint.anchor is None
                    or batch.source_session_id != target.checkpoint.source_session_id
                ):
                    raise AdapterError("session_changed")
                return batch

            def read_system(self):
                assert outer.realm.leases.get(get_ident(), 0) == 0
                outer.realm.system_calls += 1
                if outer.realm.system_failure:
                    raise AdapterError(outer.realm.system_failure)
                cp = outer.realm.batch.source_checkpoint
                return SystemObservation(
                    device_id=outer.realm.system_device,
                    start_time_raw=cp.start_time_raw,
                    source_session_id=cp.source_session_id,
                    stream_anchor=cp.anchor,
                    observed_at=outer.realm.clock.now_utc(),
                )

            def list_folders(self):
                assert outer.realm.leases.get(get_ident(), 0) == 0
                outer.realm.config_calls += 1
                if outer.realm.on_config:
                    outer.realm.on_config()
                if outer.realm.config_failure:
                    raise AdapterError(outer.realm.config_failure)
                return [
                    FolderConfig(
                        syncthing_folder_id=key,
                        label=label,
                        paused=paused,
                        observed_at=outer.realm.clock.now_utc(),
                    )
                    for key, label, paused in outer.realm.configs
                ]

            def read_runtime(self, folder):
                assert outer.realm.leases.get(get_ident(), 0) == 0
                outer.realm.runtime_calls += 1
                if folder in outer.realm.runtime_failures:
                    raise AdapterError(outer.realm.runtime_failures[folder])
                return FolderRuntime(
                    state="idle",
                    need_bytes=7,
                    pull_errors=0,
                    observed_at=outer.realm.clock.now_utc(),
                )

        return Client()

    def close(self):
        self.closed = True
        if self.realm.close_failure:
            raise RuntimeError("public-secret-close-canary")


@pytest.fixture
def realm():
    value = os.environ.get("SCC_COLLECTION_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Run collection/check_postgres.py for private SQL tests")
    url = make_url(value)
    if (
        os.environ.get("SCC_COLLECTION_TEST_PRIVATE_CLUSTER") != "a32-private-cluster"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.database != "scc_a32_tests"
        or url.username != "scc_test"
        or url.port in {None, 5432}
    ):
        pytest.fail("Refusing collection tests outside the owned private PostgreSQL")
    settings = Settings(
        database_url=value,
        connections={
            ref: ConnectionSettings(
                display_name=ref,
                base_url=f"http://127.0.0.1:{port}",
                api_key="public-collection-key-canary",
            )
            for ref, port in [("node-a", 18384), ("node-b", 28384)]
        },
    )
    database, clock = Database(settings), Clock()
    database.check_ready()
    with database.engine.begin() as connection:
        connection.execute(
            text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " CASCADE")
        )
    nodes = [uuid4(), uuid4()]
    folders = [uuid4(), uuid4()]
    with database.session() as session:
        for ref, node in zip(("node-a", "node-b"), nodes, strict=True):
            cp = checkpoint(node)
            session.add(
                ManagedNode(
                    id=node,
                    display_name=ref,
                    connection_ref=ref,
                    syncthing_device_id="device-a",
                    source_session_id=cp.source_session_id,
                    source_start_time_raw=cp.start_time_raw,
                    source_stream_anchor=cp.anchor.model_dump(mode="json"),
                )
            )
        session.flush()
        for node, identifier in zip(nodes, folders, strict=True):
            session.add(
                Folder(
                    id=identifier,
                    node_id=node,
                    syncthing_folder_id="shared",
                    label="Shared",
                    present=False,
                )
            )
        session.commit()
    state = SimpleNamespace(
        settings=settings,
        database=database,
        clock=clock,
        nodes=nodes,
        folders=folders,
        batch=packet(nodes[0], 1, 2, 3),
        failure=None,
        on_read=None,
        system_calls=0,
        system_device="device-a",
        close_failure=False,
        factories=[],
        calls=[],
        leases={},
        system_failure=None,
        config_failure=None,
        runtime_failures={},
        configs=[("shared", "Shared", False), ("control", "Control", False)],
        on_config=None,
        config_calls=0,
        runtime_calls=0,
    )

    def checkout(*_):
        state.leases[get_ident()] = state.leases.get(get_ident(), 0) + 1

    def checkin(*_):
        state.leases[get_ident()] -= 1

    event.listen(database.engine, "checkout", checkout)
    event.listen(database.engine, "checkin", checkin)

    def provider():
        factory = Factory(state)
        state.factories.append(factory)
        return factory

    state.store = EventStore(settings, database, clock)
    state.collector = EventCollector(settings, database, clock, factory_provider=provider)
    try:
        yield state
    finally:
        database.close()
