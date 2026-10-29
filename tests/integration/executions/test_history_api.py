from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

from app.models import CommandExecution, SyncRule
from fastapi.testclient import TestClient
from sqlalchemy import insert


def test_resume_is_idempotent_and_history_is_readable(
    client: TestClient, graph, postgres_engine
) -> None:
    _admin_id, node_id, folder_id = graph
    key = uuid4()
    first = client.post(
        "/api/executions/resume",
        json={"folder_id": str(folder_id), "idempotency_key": str(key)},
    )
    assert first.status_code == 202, first.text
    execution = first.json()
    assert execution["status"] == "pending"
    assert execution["source"] == "manual"

    repeated = client.post(
        "/api/executions/resume",
        json={"folder_id": str(folder_id), "idempotency_key": str(key)},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == execution["id"]

    listed = client.get("/api/executions", params={"folder_id": str(folder_id)})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert client.get(f"/api/executions/{execution['id']}").status_code == 200

    with postgres_engine.begin() as connection:
        connection.execute(
            insert(CommandExecution).values(
                id=uuid4(),
                node_id=node_id,
                folder_id=folder_id,
                source="manual",
                requested_by=_admin_id,
                idempotency_key=uuid4(),
                desired_paused=False,
                status="unknown",
                created_at=datetime(2026, 10, 29, 11, tzinfo=UTC),
                completed_at=datetime(2026, 10, 29, 11, 0, 1, tzinfo=UTC),
                error_code="verification_timeout",
            )
        )
    assert (
        client.get("/api/executions", params={"status": "unknown"}).json()["total"] == 1
    )


def test_resume_is_rejected_when_an_active_rule_controls_the_folder(
    client: TestClient, graph, postgres_engine
) -> None:
    _admin_id, _node_id, folder_id = graph

    with postgres_engine.begin() as connection:
        connection.execute(
            insert(SyncRule).values(
                id=uuid4(),
                folder_id=folder_id,
                days_mask=16,
                start_time=time(9),
                end_time=time(17),
                timezone="Europe/Moscow",
                enabled=True,
                version=1,
            )
        )
    response = client.post(
        "/api/executions/resume",
        json={"folder_id": str(folder_id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "operation_in_progress"
