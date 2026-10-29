from __future__ import annotations

from app.api.rules import router
from app.config import Settings
from app.main import create_app
from app.models import SyncRule
from fastapi.testclient import TestClient
from sqlalchemy import select


def payload(folder_id: str, **changes) -> dict:
    return {
        "folder_id": folder_id,
        "days_of_week": [5],
        "start_time": "23:00",
        "end_time": "02:00",
        "timezone": "Europe/Moscow",
        "enabled": True,
        **changes,
    }


def test_crud_and_optimistic_versioning(
    client: TestClient, graph, postgres_engine
) -> None:
    _node_id, folder_id = graph
    created = client.post("/api/rules", json=payload(str(folder_id)))
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["days_of_week"] == [5]
    assert rule["version"] == 1

    listed = client.get("/api/rules", params={"folder_id": str(folder_id)})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == rule["id"]
    assert client.get(f"/api/rules/{rule['id']}").json()["id"] == rule["id"]

    changed = client.patch(
        f"/api/rules/{rule['id']}",
        json={"expected_version": 1, "start_time": "22:00"},
    )
    assert changed.status_code == 200
    assert changed.json()["start_time"] == "22:00"
    assert changed.json()["version"] == 2

    no_op = client.patch(
        f"/api/rules/{rule['id']}",
        json={"expected_version": 2, "start_time": "22:00"},
    )
    assert no_op.status_code == 200
    assert no_op.json()["version"] == 2

    stale = client.patch(
        f"/api/rules/{rule['id']}",
        json={"expected_version": 1, "enabled": False},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    assert (
        client.delete(
            f"/api/rules/{rule['id']}", params={"expected_version": 1}
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/rules/{rule['id']}", params={"expected_version": 2}
        ).status_code
        == 204
    )
    assert client.get(f"/api/rules/{rule['id']}").status_code == 404
    with postgres_engine.connect() as connection:
        assert connection.scalar(select(SyncRule.id)) is None


def test_only_one_active_rule_and_invalid_business_states(
    client: TestClient, graph
) -> None:
    _node_id, folder_id = graph
    first = client.post("/api/rules", json=payload(str(folder_id)))
    assert first.status_code == 201
    second = client.post(
        "/api/rules", json=payload(str(folder_id), start_time="08:00", end_time="09:00")
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "active_rule_conflict"

    invalid_window = client.post(
        "/api/rules", json=payload(str(folder_id), start_time="09:00", end_time="09:00")
    )
    assert invalid_window.status_code == 422
    assert invalid_window.json()["error"]["code"] == "invalid_window"
    invalid_timezone = client.post(
        "/api/rules", json=payload(str(folder_id), timezone="Mars/Phobos")
    )
    assert invalid_timezone.status_code == 422
    assert invalid_timezone.json()["error"]["code"] == "invalid_timezone"


def test_patch_rejects_equal_merged_window_boundaries(
    client: TestClient, graph
) -> None:
    _node_id, folder_id = graph
    created = client.post("/api/rules", json=payload(str(folder_id)))
    assert created.status_code == 201
    rule = created.json()

    equal_end = client.patch(
        f"/api/rules/{rule['id']}",
        json={"expected_version": 1, "end_time": "23:00"},
    )
    assert equal_end.status_code == 422
    assert equal_end.json()["error"]["code"] == "invalid_window"

    equal_start = client.patch(
        f"/api/rules/{rule['id']}",
        json={"expected_version": 1, "start_time": "02:00"},
    )
    assert equal_start.status_code == 422
    assert equal_start.json()["error"]["code"] == "invalid_window"

    unchanged = client.get(f"/api/rules/{rule['id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["version"] == 1
    assert unchanged.json()["start_time"] == "23:00"
    assert unchanged.json()["end_time"] == "02:00"


def test_rules_router_is_behind_shared_admin_dependency() -> None:
    app = create_app(settings=Settings(), routers=[router])
    with TestClient(app) as client:
        response = client.get("/api/rules")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"
