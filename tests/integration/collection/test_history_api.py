import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.api.events import get_history, router
from app.collection.history import HistoryService
from app.config import Settings
from app.contracts import dto
from app.contracts.interfaces import SystemClock
from app.db import Database
from app.main import create_app
from app.registry.admin import AdminRecord
from app.registry.authentication import AdministratorAuth
from app.registry.security import PasswordHasher, SessionCodec
from fastapi.testclient import TestClient


def application(database=None):
    settings = Settings(
        database_url=None, session_secret="public-history-session-secret-canary" * 2
    )
    clock = SystemClock()
    admin = AdminRecord(uuid4(), "admin", PasswordHasher.dummy_hash())
    auth = AdministratorAuth(settings, SimpleNamespace(administrator=lambda: admin), clock)
    cookie, _ = SessionCodec(settings.session_secret.get_secret_value()).issue(
        admin.id, clock.now_utc()
    )
    return create_app(settings, database=database, auth=auth, routers=(router,)), cookie


@pytest.mark.parametrize("path", ["/api/events", "/api/snapshots"])
def test_history_is_guarded_before_dependencies_and_validation(path):
    app, _ = application()
    touched = []
    app.dependency_overrides[get_history] = lambda: touched.append(True)
    with TestClient(app) as client:
        assert client.get(path + "?limit=0").status_code == 401
    assert touched == []


def test_two_history_operations_match_existing_openapi():
    app, _ = application()
    actual = app.openapi()
    expected = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/design/a/openapi.json").read_text(
            encoding="utf-8"
        )
    )
    for path in ("/api/events", "/api/snapshots"):
        before, after = expected["paths"][path]["get"], actual["paths"][path]["get"]
        assert after["operationId"] == before["operationId"]
        assert set(after["responses"]) == set(before["responses"])
        assert after["security"] == expected["security"]
        params = {p["name"]: p for p in after["parameters"]}
        assert set(params) == {p["name"] for p in before["parameters"]}
        for parameter in before["parameters"]:
            observed = params[parameter["name"]]
            assert observed["required"] == parameter["required"]
            for key, value in parameter["schema"].items():
                assert observed["schema"][key] == value


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "node_id=bad",
        "folder_id=bad",
        "from=2026-10-26T00:00:00",
        "from=2026-10-26T00:00:00%2B00:00",
        "from=2026-10-26T01:00:00Z&to=2026-10-26T00:00:00Z",
        "from=2026-10-26T00:00:00Z&to=2026-10-26T00:00:00Z",
    ],
)
def test_invalid_history_filters_are_safe_422(query):
    app, cookie = application()
    history = MagicMock(spec=HistoryService)
    app.dependency_overrides[get_history] = lambda: history
    with TestClient(app) as client:
        client.cookies.set("scc_session", cookie)
        response = client.get("/api/events?" + query)
        assert response.status_code == 422
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert history.events.call_count == 0


def test_history_unavailable_returns_503_without_configuration():
    app, cookie = application(Database(Settings(database_url=None)))
    with TestClient(app) as client:
        client.cookies.set("scc_session", cookie)
        response = client.get("/api/snapshots")
        assert (
            response.status_code == 503
            and response.json()["error"]["code"] == "service_unavailable"
        )
        assert "postgresql" not in response.text and "Traceback" not in response.text


def test_scope_is_validated_without_requiring_origin_for_reads():
    app, cookie = application()
    history = MagicMock(spec=HistoryService)
    history.snapshots.return_value = dto.SnapshotPage(items=[], total=0, limit=50, offset=0)
    app.dependency_overrides[get_history] = lambda: history
    with TestClient(app) as client:
        client.cookies.set("scc_session", cookie)
        assert client.get("/api/snapshots?scope=node").status_code == 200
        assert history.snapshots.call_args.args[0].scope == "node"
        assert client.get("/api/snapshots?scope=other").status_code == 422
