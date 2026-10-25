import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from app.config import ConnectionSettings, Settings
from app.contracts.interfaces import (
    AdapterError,
    FolderConfig,
    FolderRuntime,
    StreamAnchor,
    SystemObservation,
)
from app.db import Database
from app.models import Base
from app.registry.resolver import NodeResolver
from app.registry.service import RegistryService
from sqlalchemy import event, text
from sqlalchemy.engine import make_url


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def now_utc(self):
        return self.now

    def advance(self, seconds=31):
        self.now += timedelta(seconds=seconds)


class Source:
    def __init__(self, device):
        self.device = device
        self.folders = [("shared", "Shared", False), ("control", "Control", False)]
        self.error = None
        self.runtime_error = None
        self.generation = 1
        self.on_list = None


class Adapter:
    def __init__(self, realm, connection, expected):
        self.realm, self.connection, self.expected = realm, connection, expected
        self.source = realm.sources[connection.base_url.get_secret_value()]
        self.closed = False

    def network(self):
        assert self.realm.leases.get(get_ident(), 0) == 0, "Network under a SQL transaction"
        self.realm.calls.append(self.connection.node_id)
        if self.source.error:
            raise AdapterError(self.source.error)
        if self.expected is not None and self.expected != self.source.device:
            raise AdapterError("identity_mismatch")

    def read_system(self):
        self.network()
        generation = self.source.generation
        return SystemObservation.from_source(
            node_id=self.connection.node_id,
            device_id=self.source.device,
            start_time_raw=f"2026-10-25T00:00:{generation:02d}Z",
            observed_at=self.realm.clock.now_utc(),
            stream_anchor=StreamAnchor(
                id=1, time_raw="2026-10-25T00:00:00.0000001Z", fingerprint=f"{generation:064x}"
            ),
        )

    def list_folders(self):
        self.network()
        if self.source.on_list:
            self.source.on_list()
        return tuple(
            FolderConfig(
                syncthing_folder_id=identifier,
                label=label,
                paused=paused,
                observed_at=self.realm.clock.now_utc(),
            )
            for identifier, label, paused in self.source.folders
        )

    def read_runtime(self, folder):
        self.network()
        if self.source.runtime_error:
            raise AdapterError(self.source.runtime_error)
        return FolderRuntime(
            state="idle", need_bytes=0, pull_errors=0, observed_at=self.realm.clock.now_utc()
        )

    def close(self):
        self.closed = True

    def set_folder_paused(self, *_):
        pytest.fail("Registry must not change source configuration")


class Factory:
    def __init__(self, realm):
        self.realm = realm
        self.clients = []

    def for_node(self, identifier):
        target = NodeResolver(self.realm.settings, self.realm.database)(identifier)
        client = Adapter(self.realm, target.connection, target.expected_device_id)
        self.clients.append(client)
        return client

    def close(self):
        for client in self.clients:
            client.close()


@pytest.fixture
def realm():
    value = os.environ.get("SCC_AUTH_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Real PostgreSQL: run auth/check_postgres.py --suite registry")
    url = make_url(value)
    if (
        os.environ.get("SCC_AUTH_TEST_PRIVATE_CLUSTER") != "a22-private-cluster"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.database != "scc_a22_tests"
        or url.username != "scc_test"
        or not url.port
        or url.port == 5432
    ):
        pytest.fail("Refusing registry tests outside the owned private PostgreSQL")
    database = Database(Settings(database_url=value))
    clock = Clock()
    settings = Settings(
        session_secret="public-registry-signing-secret-canary" * 2,
        connections={
            ref: ConnectionSettings(
                display_name=name, base_url=address, api_key="public-registry-key-canary"
            )
            for ref, name, address in [
                ("node-a", "Alpha", "http://127.0.0.1:18384"),
                ("node-b", "Beta", "http://127.0.0.1:28384"),
            ]
        },
    )
    state = SimpleNamespace(
        database=database,
        clock=clock,
        settings=settings,
        calls=[],
        probes=[],
        leases={},
        sources={
            url: Source(device)
            for url, device in [
                ("http://127.0.0.1:18384", "device-a"),
                ("http://127.0.0.1:28384", "device-b"),
            ]
        },
    )

    def checked_out(*_):
        state.leases[get_ident()] = state.leases.get(get_ident(), 0) + 1

    def checked_in(*_):
        state.leases[get_ident()] = state.leases.get(get_ident(), 0) - 1

    event.listen(database.engine, "checkout", checked_out)
    event.listen(database.engine, "checkin", checked_in)
    state.factory = Factory(state)

    def probe(connection, expected):
        adapter = Adapter(state, connection, expected)
        state.probes.append(adapter)
        return adapter

    state.service = RegistryService(settings, database, clock, state.factory, probe_factory=probe)
    try:
        config = Config(str(Path(__file__).resolve().parents[3] / "backend/alembic.ini"))
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
            connection.execute(text(f"TRUNCATE TABLE {tables}"))
        yield state
    finally:
        state.factory.close()
        database.close()
