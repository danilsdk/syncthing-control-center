"""Test-only composition: real A2/B1/B2 and SQL; deterministic adapter doubles.

Never loaded by the product entry point. No Syncthing or personal files are used.
"""

import threading
from contextlib import asynccontextmanager

from app.api.executions import router as executions_router
from app.api.rules import router as rules_router
from app.config import ConnectionSettings, Settings
from app.contracts.dto import NodeCreate, RuleFields
from app.contracts.interfaces import (
    FolderConfig,
    FolderRuntime,
    StreamAnchor,
    SystemClock,
    SystemObservation,
)
from app.contracts.types import mask_to_days, time_to_hhmm
from app.db import Database
from app.main import create_app, run_worker
from app.models import ManagedNode
from app.reconciliation.service import ReconciliationRuntime
from app.registry.admin import create_administrator
from app.registry.service import RegistryService
from app.scheduling.engine import calculate_desired_paused


class Adapter:
    def __init__(self, identifier, source):
        self.identifier, self.source = identifier, source
        self.clock = SystemClock()

    def read_system(self):
        return SystemObservation.from_source(
            node_id=self.identifier,
            device_id=self.source["device"],
            start_time_raw="2026-11-06T00:00:00Z",
            observed_at=self.clock.now_utc(),
            stream_anchor=StreamAnchor(id=1, time_raw="2026-11-06T00:00:00Z", fingerprint="a" * 64),
        )

    def list_folders(self):
        return [self.read_folder("shared")]

    def read_folder(self, _identifier):
        return FolderConfig(
            syncthing_folder_id="shared",
            label="Demo documents",
            paused=self.source["paused"],
            observed_at=self.clock.now_utc(),
        )

    def read_runtime(self, _identifier):
        return FolderRuntime(
            state="idle", need_bytes=0, pull_errors=0, observed_at=self.clock.now_utc()
        )

    def set_paused(self, _identifier, paused):
        self.source["paused"] = paused

    def restart_required(self):
        return False

    def close(self):
        pass


class Factory:
    def __init__(self, database):
        self.database = database
        self.sources = {
            f"node-{name}": {"device": f"E2E-DEVICE-{name}", "paused": True} for name in ["a", "b"]
        }

    def probe(self, connection, _expected):
        reference = (
            "node-a" if connection.base_url.get_secret_value().endswith(":18384") else "node-b"
        )
        return Adapter(connection.node_id, self.sources[reference])

    def for_node(self, identifier):
        with self.database.session() as session:
            node = session.get(ManagedNode, identifier)
            return Adapter(identifier, self.sources[node.connection_ref])

    def close(self):
        pass


def desired(row, now):
    return calculate_desired_paused(
        RuleFields(
            timezone=row.timezone,
            days_of_week=mask_to_days(row.days_mask),
            start_time=time_to_hhmm(row.start_time),
            end_time=time_to_hhmm(row.end_time),
            enabled=row.enabled,
        ),
        now,
    )


def create_test_app():
    settings = Settings(
        allowed_origins=["http://127.0.0.1:4173"],
        observation_interval_seconds=1,
        reconciliation_interval_seconds=1,
        connections={
            f"node-{name}": ConnectionSettings(
                display_name=f"E2E {name}",
                base_url=f"http://127.0.0.1:{port}",
                api_key="public-test-canary",
            )
            for name, port in [("a", 18384), ("b", 28384)]
        },
    )
    database = Database(settings)
    database.check_ready()
    create_administrator(database, "admin", "public-browser-test-password")
    factory = Factory(database)
    registry = RegistryService(
        settings,
        database,
        SystemClock(),
        factory,
        probe_factory=factory.probe,
        desired_state=desired,
    )
    for name in ["a", "b"]:
        registry.register(
            NodeCreate(connection_ref=f"node-{name}", display_name=f"E2E {name.upper()}")
        )
    app = create_app(
        settings,
        database,
        routers=[rules_router, executions_router],
        adapters=factory,
        registry=registry,
    )
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        stop = threading.Event()
        # The production singleton helper owns a distinct worker database pool.
        worker = threading.Thread(
            target=run_worker,
            args=(ReconciliationRuntime(stop),),
            kwargs={"settings": settings, "database": Database(settings), "adapters": factory},
            daemon=True,
        )
        worker.start()
        try:
            async with original(application):
                yield
        finally:
            stop.set()
            worker.join(timeout=15)
            if worker.is_alive():
                raise RuntimeError("Owned test worker did not stop")

    app.router.lifespan_context = lifespan
    return app
