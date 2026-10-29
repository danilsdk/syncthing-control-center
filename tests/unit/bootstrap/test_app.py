import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.api.auth import require_admin
from app.config import Settings
from app.db import Database, DatabaseNotReady
from app.main import create_app
from fastapi import APIRouter
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

ROOT = Path(__file__).resolve().parents[3]
SPEC = json.loads((ROOT / "docs/design/a/openapi.json").read_text(encoding="utf-8"))


def assert_error(response, status, code):
    assert response.status_code == status
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/components/schemas/Error",
        "components": SPEC["components"],
    }
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(response.json())
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]
    UUID(error["request_id"])


def test_liveness_is_independent_of_database_and_readiness_is_not():
    with TestClient(create_app(Settings())) as client:
        live = client.get("/health/live")
        assert live.status_code == 200 and live.json() == {"status": "ok"}
        UUID(live.headers["X-Request-ID"])
        assert_error(client.get("/health/ready"), 503, "service_unavailable")


@pytest.mark.parametrize(
    "reason", ["database_not_configured", "database_unavailable", "schema_not_ready"]
)
def test_health_failure_is_structured(reason):
    database = MagicMock(spec=Database)
    database.check_ready.side_effect = DatabaseNotReady(reason)
    with TestClient(create_app(Settings(), database)) as client:
        assert_error(client.get("/health/ready"), 503, "service_unavailable")
        assert client.get("/health/live").status_code == 200
    database.close.assert_called_once_with()


def test_readiness_succeeds_only_after_database_check():
    database = MagicMock(spec=Database)
    with TestClient(create_app(Settings(), database)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    database.check_ready.assert_called_once_with()


@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_domain_routers_are_closed_even_with_fabricated_credentials(method):
    router = APIRouter()
    touched = []

    @router.api_route("/test-protected", methods=["GET", "POST", "PATCH", "DELETE"])
    def protected():
        touched.append(True)
        return {"changed": True}

    with TestClient(create_app(Settings(), routers=[router])) as client:
        client.cookies.set("scc_session", "fabricated-cookie")
        response = getattr(client, method)(
            "/api/test-protected", headers={"Authorization": "Bearer fabricated"}
        )
        assert_error(response, 503, "auth_not_configured")
    assert not touched


class Payload(BaseModel):
    password: int


def test_validation_error_never_echoes_input_or_custom_error_messages(caplog):
    router = APIRouter()

    @router.post("/test-validation")
    def validate(payload: Payload):
        return {"accepted": True}

    app = create_app(Settings(), routers=[router])
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as client:
        response = client.post("/api/test-validation", json={"password": "submitted-secret-canary"})
    assert_error(response, 422, "validation_error")
    assert "submitted-secret-canary" not in response.text + caplog.text


@pytest.mark.parametrize("kind", ["unexpected", "http", "database"])
def test_errors_do_not_leak_exception_detail_or_traceback(kind, caplog):
    router = APIRouter()

    @router.get("/test-error")
    def fail():
        if kind == "http":
            raise HTTPException(400, "private-exception-canary")
        if kind == "database":
            raise SQLAlchemyError("private-exception-canary")
        raise RuntimeError("private-exception-canary")

    app = create_app(Settings(), routers=[router])
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as client:
        response = client.get("/api/test-error")
    status, code = {
        "http": (400, "request_error"),
        "database": (503, "service_unavailable"),
        "unexpected": (500, "internal_error"),
    }[kind]
    assert_error(response, status, code)
    assert "private-exception-canary" not in response.text + caplog.text


def test_request_ids_are_generated_by_server_and_framework_errors_are_uniform():
    with TestClient(create_app(Settings())) as client:
        supplied = "11111111-1111-4111-8111-111111111111"
        missing = client.get("/not-implemented", headers={"X-Request-ID": supplied})
        assert_error(missing, 404, "not_found")
        assert missing.headers["X-Request-ID"] != supplied
        wrong_method = client.post("/health/live")
        assert_error(wrong_method, 405, "method_not_allowed")
        assert "GET" in wrong_method.headers["Allow"]
