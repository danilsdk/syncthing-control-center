import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from unittest.mock import MagicMock
from uuid import uuid4

import app.main as main_module
import pytest
from app.api.auth import require_admin
from app.api.dependencies import get_adapter, get_adapter_factory, get_clock, get_folder_locks
from app.config import Settings
from app.contracts.dto import RuleCreate
from app.contracts.interfaces import AdapterFactory, Clock, SyncthingAdapter
from app.contracts.locks import AdvisoryLocks, LockBusy, LockLost
from app.db import Database, DatabaseNotReady
from app.main import create_app, run_worker
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


def test_composition_dependencies_use_injected_resources_and_close_the_factory():
    clock = MagicMock(spec=Clock)
    clock.now_utc.return_value = datetime(2026, 10, 24, 12, tzinfo=UTC)
    adapters = MagicMock(spec=AdapterFactory)
    router = APIRouter()

    @router.get("/nodes/{node_id}/test-adapter")
    def use_adapter(
        adapter: Annotated[SyncthingAdapter, Depends(get_adapter)],
        current_clock: Annotated[Clock, Depends(get_clock)],
    ):
        return {"time": current_clock.now_utc(), "restart": adapter.restart_required()}

    adapters.for_node.return_value.restart_required.return_value = False
    app = create_app(Settings(), routers=[router], clock=clock, adapters=adapters)
    app.dependency_overrides[require_admin] = lambda: None
    node_id = uuid4()
    with TestClient(app) as client:
        response = client.get(f"/api/nodes/{node_id}/test-adapter")
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["time"]) == clock.now_utc.return_value
    assert response.json()["restart"] is False
    adapters.for_node.assert_called_once_with(node_id)
    adapters.close.assert_called_once_with()


def test_default_dependencies_do_not_invent_an_adapter_or_database():
    router = APIRouter()

    @router.get("/test-adapters")
    def adapters(factory: Annotated[AdapterFactory, Depends(get_adapter_factory)]):
        pytest.fail("Missing adapter must not reach the handler")

    @router.get("/test-locks")
    def locks(manager: Annotated[AdvisoryLocks, Depends(get_folder_locks)]):
        pytest.fail("Missing database must not reach the handler")

    app = create_app(Settings(), routers=[router])
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as client:
        for path in ["test-adapters", "test-locks"]:
            response = client.get("/api/" + path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "service_unavailable"


def test_authorization_runs_before_adapter_resolution():
    adapters = MagicMock(spec=AdapterFactory)
    router = APIRouter()

    @router.get("/nodes/{node_id}/test-adapter")
    def protected(adapter: Annotated[SyncthingAdapter, Depends(get_adapter)]):
        pytest.fail("Unconfigured authorization must block the router")

    with TestClient(create_app(Settings(), routers=[router], adapters=adapters)) as client:
        assert client.get(f"/api/nodes/{uuid4()}/test-adapter").status_code == 503
    adapters.for_node.assert_not_called()


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("timezone", "Not/AZone", "invalid_timezone"),
        ("end_time", "23:00", "invalid_window"),
        ("days_of_week", [5, 5], "validation_error"),
    ],
)
def test_dto_errors_have_contract_codes_without_echoing_input(field, value, code):
    router = APIRouter()

    @router.post("/test-rule")
    def rule(payload: RuleCreate):
        return payload

    app = create_app(Settings(), routers=[router])
    app.dependency_overrides[require_admin] = lambda: None
    payload = {
        "folder_id": str(uuid4()),
        "days_of_week": [5],
        "start_time": "23:00",
        "end_time": "02:00",
        "timezone": "Europe/Moscow",
    } | {field: value}
    with TestClient(app) as client:
        response = client.post("/api/test-rule", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert "Not/AZone" not in response.text


@pytest.mark.parametrize(
    "error,status,code",
    [
        (LockBusy, 409, "operation_in_progress"),
        (LockLost, 503, "service_unavailable"),
    ],
)
def test_lock_failures_have_safe_api_errors(error, status, code):
    router = APIRouter()

    @router.get("/test-lock")
    def lock():
        raise error()

    app = create_app(Settings(), routers=[router])
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as client:
        response = client.get("/api/test-lock")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "failure,expected",
    [(None, 0), (LockBusy, 3), (LockLost, 4), (RuntimeError, 1), (KeyboardInterrupt, 130)],
)
def test_worker_is_an_explicit_singleton_and_closes_resources(
    monkeypatch, capsys, failure, expected
):
    db = MagicMock(spec=Database)
    db.engine = MagicMock()
    adapters = MagicMock(spec=AdapterFactory)
    runtime = MagicMock()
    singleton = MagicMock()

    @contextmanager
    def lock_worker():
        if failure in {LockBusy, LockLost}:
            raise failure()
        yield singleton

    locks = MagicMock()
    locks.worker = lock_worker
    monkeypatch.setattr(main_module, "AdvisoryLocks", lambda engine: locks)
    if failure in {RuntimeError, KeyboardInterrupt}:
        runtime.run.side_effect = failure("runtime-secret-canary")
    result = run_worker(runtime, settings=Settings(), database=db, adapters=adapters)
    assert result == expected
    if failure in {LockBusy, LockLost}:
        runtime.run.assert_not_called()
    else:
        context = runtime.run.call_args.args[0]
        assert context.database is db and context.folder_locks is locks
        singleton.ensure_held.assert_called_once_with()
    assert "runtime-secret-canary" not in capsys.readouterr().err
    db.close.assert_called_once_with()
    adapters.close.assert_called_once_with()


def test_worker_does_not_run_with_missing_database_or_runtime(capsys):
    db = MagicMock(spec=Database)
    adapters = MagicMock(spec=AdapterFactory)
    runtime = MagicMock()
    db.check_ready.side_effect = DatabaseNotReady("schema_not_ready")
    assert run_worker(runtime, settings=Settings(), database=db, adapters=adapters) == 4
    runtime.run.assert_not_called()
    assert run_worker(None) == 2
    assert "Worker" in capsys.readouterr().err


def test_worker_cli_exits_unconfigured_without_starting_a_scheduler():
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SCC_")}
    result = subprocess.run(
        [sys.executable, "-m", "app.main", "worker"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 2
    assert "Worker runtime and adapter factory must be configured" in result.stderr
    assert "Traceback" not in result.stderr


def test_worker_rejects_pool_that_cannot_hold_worker_and_folder_connections(capsys):
    runtime = MagicMock()
    adapters = MagicMock(spec=AdapterFactory)
    db = MagicMock(spec=Database)
    assert (
        run_worker(runtime, settings=Settings(db_pool_size=1), database=db, adapters=adapters) == 2
    )
    runtime.run.assert_not_called()
    db.check_ready.assert_not_called()
    db.close.assert_called_once_with()
    assert "pool size of at least 2" in capsys.readouterr().err


def test_cleanup_closes_database_even_if_factory_fails_without_leaking_error(caplog, capsys):
    db = MagicMock(spec=Database)
    adapters = MagicMock(spec=AdapterFactory)
    adapters.close.side_effect = RuntimeError("cleanup-secret-canary")
    assert run_worker(None, database=db, adapters=adapters) == 1
    db.close.assert_called_once_with()
    assert "cleanup-secret-canary" not in caplog.text + capsys.readouterr().err
