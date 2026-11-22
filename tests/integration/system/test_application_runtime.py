"""A4 composition and failure boundaries; these tests do not claim native integration."""

import importlib.util
import sys
import threading
import warnings
from contextlib import nullcontext
from datetime import UTC, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app import main as application
from app.api.events import router as events_router
from app.config import Settings
from app.contracts.errors import AppError
from app.contracts.interfaces import SystemClock
from app.contracts.locks import LockBusy, LockLost
from app.contracts.worker import WorkerContext
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "a43_runtime", ROOT / "deploy/compose/image/runtime.py"
)
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)
ID = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize(
    "path", ["events", "snapshots", "rules", f"rules/{ID}", "executions", f"executions/{ID}"]
)
def test_default_routes_require_auth_before_database_access(path):
    def reject(_token):
        raise AppError(401, "unauthenticated", "Authentication required")

    db = SimpleNamespace(close=lambda: None)
    with TestClient(
        create_app(Settings(), database=db, auth=SimpleNamespace(authenticate=reject))
    ) as client:
        response = client.get("/api/" + path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "rules"),
        ("PATCH", f"rules/{ID}"),
        ("DELETE", f"rules/{ID}"),
        ("POST", "executions/resume"),
    ],
)
@pytest.mark.parametrize(
    "headers,code",
    [({}, "origin_forbidden"), ({"Origin": "http://localhost:8000"}, "csrf_invalid")],
)
def test_default_mutations_require_origin_and_csrf(method, path, headers, code):
    auth = SimpleNamespace(authenticate=lambda _: SimpleNamespace(csrf_token="a" * 32, user=None))
    with TestClient(
        create_app(Settings(), database=SimpleNamespace(close=lambda: None), auth=auth)
    ) as client:
        response = client.request(method, "/api/" + path, headers=headers, json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == code


def test_explicit_accepted_router_is_not_registered_twice():
    app = create_app(Settings(), routers=(events_router,))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = app.openapi()
    assert schema["paths"]["/api/events"]["get"]["operationId"] == "list_events"


@pytest.mark.parametrize("hour,expected", [(12, False), (18, True)])
def test_registry_uses_b1_calculation_for_persisted_rule(hour, expected):
    app = create_app(Settings())
    rule = SimpleNamespace(
        timezone="UTC", days_mask=127, start_time=time(11), end_time=time(13), enabled=True
    )
    assert (
        app.state.registry.desired_state(rule, datetime(2026, 11, 21, hour, tzinfo=UTC)) is expected
    )


def context(engine, settings=None, singleton=None):
    return WorkerContext(
        settings or Settings(),
        SimpleNamespace(engine=engine),
        SystemClock(),
        MagicMock(),
        MagicMock(),
        singleton or SimpleNamespace(ensure_held=lambda: None),
    )


def test_stop_arriving_during_lease_verification_does_not_start_http():
    stop = threading.Event()
    activity = runtime.Activity(SimpleNamespace(ensure_active=stop.set), stop)
    adapter = runtime.ActiveAdapter(
        SimpleNamespace(read_system=lambda: pytest.fail("HTTP after stop")), activity
    )
    with pytest.raises(runtime.WorkerStopping):
        adapter.read_system()


def test_shared_worker_rejects_a_pool_without_room_for_the_real_resolver(monkeypatch):
    monkeypatch.setattr(runtime, "Database", lambda _: pytest.fail("Pool preflight missed"))
    assert runtime.worker_main(Settings(db_pool_size=2)) == 2


@pytest.mark.parametrize("stopping", [False, True])
def test_each_http_request_is_guarded_within_one_adapter_operation(stopping):
    stop, calls, lost = threading.Event(), [], [False]

    def ensure():
        if lost[0]:
            raise LockLost()

    def request(*_a, **_k):
        calls.append("request")
        if stopping:
            stop.set()
        else:
            lost[0] = True

    transport = runtime.ActiveTransport(
        SimpleNamespace(request=request, close=lambda: calls.append("close")),
        runtime.Activity(SimpleNamespace(ensure_active=ensure), stop),
    )
    transport.request("GET", "/rest/system/status")
    with pytest.raises((runtime.WorkerStopping, LockLost)):
        transport.request("GET", "/rest/events")
    transport.close()
    assert calls == ["request", "close"]


@pytest.mark.parametrize("first_pass_busy", [False, True])
def test_phases_use_independent_intervals_and_do_not_replay_missed_ticks(
    monkeypatch, first_pass_busy
):
    clock, calls = [0.0], []

    class Stop:
        def is_set(self):
            return clock[0] >= 6

        def wait(self, seconds):
            assert seconds > 0
            clock[0] += seconds

    monkeypatch.setattr(
        runtime,
        "CollectionService",
        lambda *_a, **_k: SimpleNamespace(collect_once=lambda: calls.append(("collect", clock[0]))),
    )

    def reconcile(_context):
        calls.append(("reconcile", clock[0]))
        if first_pass_busy and clock[0] == 0:
            raise LockBusy()

    monkeypatch.setattr(runtime, "run_once", reconcile)
    engine = create_engine("sqlite://")
    try:
        runtime.ApplicationRuntime(Stop(), monotonic=lambda: clock[0]).run(
            context(
                engine, Settings(observation_interval_seconds=3, reconciliation_interval_seconds=2)
            )
        )
        assert calls == [
            ("collect", 0),
            ("reconcile", 0),
            ("reconcile", 2),
            ("collect", 3),
            ("reconcile", 4),
        ]
    finally:
        engine.dispose()


def test_runtime_lock_busy_is_not_a_singleton_conflict(monkeypatch, capsys):
    database, adapters, worker = MagicMock(), MagicMock(), MagicMock()
    worker.run.side_effect = LockBusy
    locks = SimpleNamespace(worker=lambda: nullcontext(MagicMock()))
    monkeypatch.setattr(application, "AdvisoryLocks", lambda _: locks)

    assert (
        application.run_worker(worker, settings=Settings(), database=database, adapters=adapters)
        == 1
    )
    assert "A worker is already running" not in capsys.readouterr().err
    database.close.assert_called_once_with()
    adapters.close.assert_called_once_with()


@pytest.mark.parametrize("stopping", [False, True])
def test_loss_inside_collection_blocks_later_http_sql_and_reconciliation(monkeypatch, stopping):
    stop, lost, http, sql = threading.Event(), [False], [], []

    def ensure():
        if lost[0]:
            raise LockLost()

    def first_read():
        http.append("first")
        stop.set() if stopping else lost.__setitem__(0, True)

    adapter = SimpleNamespace(read_system=first_read, read_runtime=lambda: http.append("forbidden"))
    owned_factory = SimpleNamespace(for_node=lambda _: adapter, close=lambda: None)
    monkeypatch.setattr(runtime, "HttpAdapterFactory", lambda *_a, **_k: owned_factory)
    engine = create_engine("sqlite://")
    event.listen(engine, "after_cursor_execute", lambda *_a: sql.append("executed"))

    def build_collector(*_args, factory_provider):
        def collect():
            factory = factory_provider()
            client = factory.for_node(uuid4())
            client.read_system()
            # The accepted collector catches source failures; this must not permit more I/O.
            for operation in (
                client.read_runtime,
                lambda: engine.connect().execute(text("SELECT 1")),
            ):
                with pytest.raises((LockLost, runtime.WorkerStopping)):
                    operation()
            factory.close()

        return SimpleNamespace(collect_once=collect)

    monkeypatch.setattr(runtime, "CollectionService", build_collector)
    monkeypatch.setattr(runtime, "run_once", lambda _: pytest.fail("Reconciliation after loss"))
    try:
        job = runtime.ApplicationRuntime(stop, once=True)
        current = context(engine, singleton=SimpleNamespace(ensure_held=ensure))
        if stopping:
            job.run(current)
        else:
            with pytest.raises(LockLost):
                job.run(current)
        assert http == ["first"] and not sql
        # Event guards have been detached; even after loss normal cleanup SQL is possible.
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("stopping", [False, True])
def test_loss_before_implicit_commit_rolls_back_instead_of_persisting_a_result(
    monkeypatch, stopping
):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE proof (value INTEGER)"))
    stop, lost = threading.Event(), [False]

    def ensure():
        if lost[0]:
            raise LockLost()

    def collect():
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO proof VALUES (1)"))
            stop.set() if stopping else lost.__setitem__(0, True)

    monkeypatch.setattr(
        runtime, "CollectionService", lambda *_a, **_k: SimpleNamespace(collect_once=collect)
    )
    monkeypatch.setattr(runtime, "run_once", lambda _: pytest.fail("Reconciliation after loss"))
    try:
        job = runtime.ApplicationRuntime(stop, once=True)
        current = context(engine, singleton=SimpleNamespace(ensure_held=ensure))
        if stopping:
            job.run(current)
        else:
            with pytest.raises(LockLost):
                job.run(current)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM proof")) == 0
    finally:
        engine.dispose()
