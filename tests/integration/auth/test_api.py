import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.config import Settings
from app.contracts.dto import Error, Session
from app.db import DatabaseNotReady
from app.main import create_app
from app.registry.authentication import AdministratorAuth
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


def login(c, **changes):
    return c.client.post(
        "/api/auth/login",
        json={
            "login": "admin",
            "password": c.password,
            **changes,
        },
        headers={"Origin": c.origin},
    )


def error(response, status, code):
    parsed = Error.model_validate(response.json())
    assert response.status_code == status and parsed.error.code == code
    assert str(parsed.error.request_id) == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"


def test_login_me_and_shared_router_with_csrf(context):
    c = context
    response = login(c)
    assert response.status_code == 200
    session = Session.model_validate(response.json())
    assert len(c.store.logins) == 1
    assert c.client.get("/api/auth/me").json() == response.json()
    assert c.client.get("/api/test-resource").status_code == 200
    assert (
        c.client.post(
            "/api/test-resource",
            headers={
                "Origin": c.origin,
                "X-CSRF-Token": session.csrf_token,
            },
        ).status_code
        == 200
    )
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=28800" in cookie and "Domain=" not in cookie and "Secure" not in cookie
    assert c.password not in response.text and c.secret not in response.text


@pytest.mark.parametrize("method", ["get", "post", "patch", "put", "delete"])
def test_no_session_cannot_reach_shared_router(context, method):
    c = context
    error(getattr(c.client, method)("/api/test-resource"), 401, "authentication_required")
    assert not c.touched


def test_auth_runs_before_adapter_resolution(context):
    error(context.client.get(f"/api/nodes/{uuid4()}/test-source"), 401, "authentication_required")
    context.adapters.for_node.assert_not_called()


@pytest.mark.parametrize("origin", [None, "https://foreign.invalid", "null"])
@pytest.mark.parametrize("body", ["valid", "malformed"])
def test_login_origin_checked_before_body_and_kdf(context, origin, body):
    c = context
    headers = {} if origin is None else {"Origin": origin}
    kwargs = (
        {"json": {"login": "admin", "password": c.password}}
        if body == "valid"
        else {"content": "{broken"}
    )
    error(c.client.post("/api/auth/login", headers=headers, **kwargs), 403, "origin_forbidden")
    assert not c.hasher.targets


def test_duplicate_origin_is_not_accepted(context):
    c = context
    error(
        c.client.post(
            "/api/auth/login",
            json={"login": "admin", "password": c.password},
            headers=[("Origin", c.origin), ("Origin", "https://foreign.invalid")],
        ),
        403,
        "origin_forbidden",
    )


@pytest.mark.parametrize("mutation", ["post", "patch", "put", "delete"])
def test_mutations_require_origin_and_csrf(context, mutation):
    c = context
    token = login(c).json()["csrf_token"]
    method = getattr(c.client, mutation)
    error(method("/api/test-resource", headers={"X-CSRF-Token": token}), 403, "origin_forbidden")
    error(method("/api/test-resource", headers={"Origin": c.origin}), 403, "csrf_invalid")
    error(
        method("/api/test-resource", headers={"Origin": c.origin, "X-CSRF-Token": "wrong"}),
        403,
        "csrf_invalid",
    )
    assert not c.touched


def test_csrf_is_session_specific_and_duplicate_header_rejected(context):
    c = context
    old = login(c).json()["csrf_token"]
    current = login(c).json()["csrf_token"]
    assert current != old
    error(
        c.client.post("/api/test-resource", headers={"Origin": c.origin, "X-CSRF-Token": old}),
        403,
        "csrf_invalid",
    )
    error(
        c.client.post(
            "/api/test-resource",
            headers=[("Origin", c.origin), ("X-CSRF-Token", current), ("X-CSRF-Token", current)],
        ),
        403,
        "csrf_invalid",
    )


def test_logout_clears_cookie_and_protected_access(context):
    c = context
    token = login(c).json()["csrf_token"]
    response = c.client.post(
        "/api/auth/logout", headers={"Origin": c.origin, "X-CSRF-Token": token}
    )
    assert response.status_code == 204 and response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert c.client.cookies.get("scc_session") is None
    error(c.client.get("/api/auth/me"), 401, "authentication_required")


def test_session_exact_expiry_is_not_extended_by_me(context):
    c = context
    initial = Session.model_validate(login(c).json())
    c.clock.now = initial.expires_at - timedelta(seconds=1)
    assert (
        c.client.get("/api/auth/me").json()["expires_at"]
        == initial.model_dump(mode="json")["expires_at"]
    )
    c.clock.now = initial.expires_at
    error(c.client.get("/api/auth/me"), 401, "authentication_required")


@pytest.mark.parametrize(
    "change", ["inactive", "missing", "different_id", "rotated_secret", "tampered"]
)
def test_changed_user_or_key_invalidates_access(context, change):
    c = context
    login(c)
    expected = 401
    if change == "inactive":
        c.store.current = replace(c.store.current, is_active=False)
    elif change == "missing":
        c.store.current = None
        expected = 503
    elif change == "different_id":
        c.store.current = replace(c.store.current, id=uuid4())
    elif change == "rotated_secret":
        settings = Settings(session_secret="rotated-public-secret" * 3)
        c.app.state.auth = AdministratorAuth(settings, c.store, c.clock, hasher=c.hasher)
    else:
        c.client.cookies.clear()
        c.client.cookies.set("scc_session", "fabricated-token")
    error(
        c.client.get("/api/auth/me"),
        expected,
        "auth_not_configured" if expected == 503 else "authentication_required",
    )


def test_unknown_login_and_wrong_password_have_same_error_and_kdf(context):
    c = context
    unknown = login(c, login="missing")
    wrong = login(c, password="incorrect-password")
    error(unknown, 401, "invalid_credentials")
    error(wrong, 401, "invalid_credentials")
    assert len(c.hasher.targets) == 2
    assert c.hasher.targets[0] != "test-only-hash" and c.hasher.targets[1] == "test-only-hash"
    a, b = unknown.json()["error"], wrong.json()["error"]
    assert a["message"] == b["message"] and a["details"] == b["details"]


def test_inactive_login_and_user_changed_during_kdf_cannot_issue_cookie(context):
    c = context
    c.store.current = replace(c.store.current, is_active=False)
    response = login(c)
    error(response, 401, "invalid_credentials")
    assert "set-cookie" not in response.headers
    c.store.current = replace(c.store.current, is_active=True)
    c.store.changed_during_login = True
    response = login(c)
    error(response, 401, "invalid_credentials")
    assert "set-cookie" not in response.headers


def test_peer_limit_cannot_be_bypassed_with_forwarded_header(context):
    c = context
    for _ in range(5):
        assert login(c, password="wrong").status_code == 401
    response = c.client.post(
        "/api/auth/login",
        json={"login": "admin", "password": c.password},
        headers={"Origin": c.origin, "X-Forwarded-For": "192.0.2.100"},
    )
    error(response, 429, "login_rate_limited")
    assert len(c.hasher.targets) == 5
    c.clock.tick = 60
    assert login(c).status_code == 200


@pytest.mark.parametrize(
    "failure", [DatabaseNotReady("database_not_configured"), SQLAlchemyError("private-dsn-canary")]
)
def test_database_failures_are_safe(context, failure):
    context.store.failure = failure
    response = login(context)
    error(response, 503, "service_unavailable")
    assert "private-dsn-canary" not in response.text


def test_unconfigured_auth_stays_closed_and_health_stays_public():
    with TestClient(create_app(Settings())) as client:
        response = client.post(
            "/api/auth/login",
            json={"login": "admin", "password": "test"},
            headers={"Origin": "http://localhost:8000"},
        )
        error(response, 503, "auth_not_configured")
        assert client.get("/health/live").status_code == 200


def test_validation_and_unhandled_errors_do_not_expose_secrets(context, caplog):
    c = context
    response = c.client.post(
        "/api/auth/login",
        json={"login": "admin", "password": c.password, "extra": "secret"},
        headers={"Origin": c.origin},
    )
    error(response, 422, "validation_error")
    login(c)
    cookie = c.client.cookies.get("scc_session")
    response = c.client.get("/api/test-secret-failure")
    error(response, 500, "internal_error")
    assert c.password not in response.text + caplog.text
    assert cookie not in response.text + caplog.text


@pytest.mark.parametrize(
    "environment,origin", [("production", "https://scc.test"), ("development", "https://scc.test")]
)
def test_https_cookie_is_secure_on_login_and_logout(context, environment, origin):
    c = context
    settings = Settings(environment=environment, allowed_origins=(origin,), session_secret=c.secret)
    auth = AdministratorAuth(settings, c.store, c.clock, hasher=c.hasher)
    with TestClient(create_app(settings, auth=auth), base_url=origin) as client:
        response = client.post(
            "/api/auth/login",
            json={"login": "admin", "password": c.password},
            headers={"Origin": origin},
        )
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
        response = client.post(
            "/api/auth/logout",
            headers={"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]},
        )
        assert response.status_code == 204 and "Secure" in response.headers["set-cookie"]


def test_implemented_auth_openapi_matches_contract(context):
    runtime = context.app.openapi()
    root = Path(__file__).resolve().parents[3]
    design = json.loads((root / "docs/design/a/openapi.json").read_text(encoding="utf-8"))
    for path, method in [
        ("/api/auth/login", "post"),
        ("/api/auth/me", "get"),
        ("/api/auth/logout", "post"),
    ]:
        actual, expected = runtime["paths"][path][method], design["paths"][path][method]
        assert actual["operationId"] == expected["operationId"]
        assert set(actual["responses"]) == set(expected["responses"])
        assert actual.get("security") == expected.get("security", design["security"])
        assert actual.get("parameters", []) == expected.get("parameters", [])
    assert runtime["components"]["securitySchemes"]["adminSession"]["name"] == "scc_session"


def test_corrupt_configured_hash_closes_login_without_cookie(context):
    context.store.current = replace(context.store.current, password_hash="corrupted-private-hash")
    response = login(context)
    error(response, 503, "auth_not_configured")
    assert "set-cookie" not in response.headers
    assert "corrupted-private-hash" not in response.text
