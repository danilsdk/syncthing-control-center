import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.config import ConnectionSettings, Settings
from app.contracts.interfaces import SystemClock
from app.main import create_app
from app.registry.admin import AdminRecord
from app.registry.authentication import AdministratorAuth
from app.registry.security import PasswordHasher, SessionCodec
from app.registry.service import RegistryService
from fastapi.testclient import TestClient


def authenticated_app(**kwargs):
    settings = Settings(
        session_secret="public-registry-api-contract-secret" * 2,
        connections={
            "second": ConnectionSettings(
                display_name="Same", base_url="http://127.0.0.1:2", api_key="public-key-two"
            ),
            "first": ConnectionSettings(
                display_name="Same", base_url="http://127.0.0.1:1", api_key="public-key-one"
            ),
        },
    )
    clock = SystemClock()
    admin = AdminRecord(uuid4(), "admin", PasswordHasher.dummy_hash())
    auth = AdministratorAuth(settings, SimpleNamespace(administrator=lambda: admin), clock)
    cookie, _ = SessionCodec(settings.session_secret.get_secret_value()).issue(
        admin.id, clock.now_utc()
    )
    return create_app(settings, clock=clock, auth=auth, **kwargs), cookie


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "/api/connections", None),
        ("get", "/api/nodes", None),
        ("post", "/api/nodes", {"connection_ref": "first", "display_name": "First"}),
        ("get", f"/api/nodes/{uuid4()}", None),
        ("patch", f"/api/nodes/{uuid4()}", {"enabled": False}),
        ("get", f"/api/nodes/{uuid4()}/folders", None),
    ],
)
def test_all_registry_operations_are_guarded_before_service(method, path, payload):
    registry = MagicMock(spec=RegistryService)
    app, _ = authenticated_app(registry=registry)
    with TestClient(app) as client:
        response = getattr(client, method)(
            path, **({"json": payload} if payload is not None else {})
        )
        assert response.status_code == 401
    assert not registry.mock_calls


def test_connections_page_is_stable_and_contains_only_public_aliases():
    app, cookie = authenticated_app()
    with TestClient(app) as client:
        client.cookies.set("scc_session", cookie)
        first = client.get("/api/connections?limit=1&offset=0")
        second = client.get("/api/connections?limit=1&offset=1")
        assert first.json()["items"] == [{"connection_ref": "first", "display_name": "Same"}]
        assert second.json()["items"][0]["connection_ref"] == "second"
        assert first.json()["total"] == 2
        assert "public-key" not in first.text and "base_url" not in first.text
        assert client.get("/api/connections?limit=101").status_code == 422


def test_all_nine_implemented_operations_match_design():
    app, _ = authenticated_app()
    actual = app.openapi()
    expected = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/design/a/openapi.json").read_text(
            encoding="utf-8"
        )
    )
    operations = 0
    for path, methods in expected["paths"].items():
        if not path.startswith(("/api/auth", "/api/connections", "/api/nodes")):
            continue
        for method, definition in methods.items():
            if method not in {"get", "post", "patch"}:
                continue
            operation = actual["paths"][path][method]
            assert operation["operationId"] == definition["operationId"]
            assert set(operation["responses"]) == set(definition["responses"])
            assert operation.get("security") == definition.get("security", expected["security"])
            supplied = {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
            for parameter in definition.get("parameters", []):
                observed = supplied[(parameter["in"], parameter["name"])]
                assert observed["required"] == parameter["required"]
                for key, value in parameter["schema"].items():
                    assert observed["schema"][key] == value
            operations += 1
    assert operations == 9
