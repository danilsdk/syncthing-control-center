from uuid import uuid4

import pytest
from app.api.analytics import router
from app.api.auth import require_admin
from app.api.dependencies import get_session
from app.config import Settings
from app.contracts.dto import User
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

PATH = "/api/analytics/summary"
PERIOD = {"from": "2026-11-01T23:30:00Z", "to": "2026-11-02T11:00:00Z"}


def test_summary_is_closed_without_administrator_configuration():
    app = create_app(Settings(session_secret=None, database_url=None), routers=(router,))
    with TestClient(app) as client:
        response = client.get(PATH, params=PERIOD)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "auth_not_configured"


@pytest.fixture
def app():
    application = create_app(Settings(database_url=None), routers=(router,))
    application.dependency_overrides[require_admin] = lambda: User(id=uuid4(), login="admin")
    return application


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"to": "2026-11-01T23:30:00Z"}, "invalid_period"),
        ({"to": "2026-12-03T00:00:00Z"}, "invalid_period"),
        ({"from": "2026-11-01T23:30:00+00:00"}, "validation_error"),
        ({"source": "invalid"}, "validation_error"),
        ({"node_id": "invalid"}, "validation_error"),
    ],
)
def test_invalid_filters(app, params, code):
    with Session() as session, TestClient(app) as client:
        app.dependency_overrides[get_session] = lambda: session
        response = client.get(PATH, params=PERIOD | params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_unconfigured_database_uses_shared_error(app):
    with TestClient(app) as client:
        response = client.get(PATH, params=PERIOD)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
