from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

from app.models import CommandExecution, SyncRule
from fastapi.testclient import TestClient
from sqlalchemy import event, insert


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
    assert client.get("/api/executions", params={"status": "unknown"}).json()["total"] == 1


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


def test_history_uses_one_snapshot_when_a_writer_commits_after_count(
    client: TestClient, graph, postgres_engine
) -> None:
    admin_id, node_id, folder_id = graph
    committed = False

    def insert_after_count(_conn, _cursor, statement, _params, _context, _many):
        nonlocal committed
        normalized = " ".join(statement.lower().split())
        if committed or "select count(" not in normalized or "command_executions" not in normalized:
            return
        committed = True
        with postgres_engine.begin() as writer:
            writer.execute(
                insert(CommandExecution).values(
                    id=uuid4(),
                    node_id=node_id,
                    folder_id=folder_id,
                    source="manual",
                    requested_by=admin_id,
                    idempotency_key=uuid4(),
                    desired_paused=False,
                    status="pending",
                    created_at=datetime(2026, 10, 29, 11, tzinfo=UTC),
                )
            )

    event.listen(postgres_engine, "after_cursor_execute", insert_after_count)
    try:
        response = client.get("/api/executions")
    finally:
        event.remove(postgres_engine, "after_cursor_execute", insert_after_count)
    assert committed
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert response.json()["items"] == []
    # The next request sees the independent writer's committed attempt.
    current = client.get("/api/executions").json()
    assert current["total"] == len(current["items"]) == 1


def test_history_total_survives_an_empty_offset_page_and_respects_filters(
    client: TestClient, graph
) -> None:
    created = client.post(
        "/api/executions/resume",
        json={"folder_id": str(graph[2]), "idempotency_key": str(uuid4())},
    )
    assert created.status_code == 202
    for params, expected in [
        ({"offset": 1}, 1),
        ({"offset": 100, "status": "pending", "node_id": str(graph[1])}, 1),
        ({"status": "unknown"}, 0),
        ({"folder_id": str(uuid4())}, 0),
    ]:
        response = client.get("/api/executions", params=params)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
        assert response.json()["items"] == []
