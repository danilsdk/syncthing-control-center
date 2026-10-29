"""Database invariants tested by PostgreSQL, not an SQLite approximation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.config import Settings
from app.db import Database, DatabaseNotReady
from app.main import create_app
from app.models import (
    AppUser,
    Base,
    CommandExecution,
    Folder,
    ManagedNode,
    StatusSnapshot,
    SyncEvent,
    SyncRule,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .conftest import migrate

pytestmark = pytest.mark.postgres
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
SESSION = "a" * 64


def seed(pg):
    graph = SimpleNamespace(
        user=uuid4(),
        node=uuid4(),
        other_node=uuid4(),
        folder=uuid4(),
        other_folder=uuid4(),
        rule=uuid4(),
    )
    with pg.begin() as connection:
        connection.execute(
            insert(AppUser).values(
                id=graph.user, login="admin", password_hash="test-hash-not-a-real-password"
            )
        )
        for node, alias in [(graph.node, "node-a"), (graph.other_node, "node-b")]:
            connection.execute(
                insert(ManagedNode).values(id=node, display_name=alias, connection_ref=alias)
            )
        for folder, node in [(graph.folder, graph.node), (graph.other_folder, graph.other_node)]:
            connection.execute(
                insert(Folder).values(
                    id=folder, node_id=node, syncthing_folder_id="same-external-id", label="Demo"
                )
            )
        connection.execute(insert(SyncRule).values(id=graph.rule, **rule_values(graph)))
    return graph


def rule_values(graph, **changes):
    return (
        dict(folder_id=graph.folder, days_mask=16, start_time=time(23), end_time=time(2)) | changes
    )


def execution_values(graph, **changes):
    return (
        dict(
            node_id=graph.node,
            folder_id=graph.folder,
            source="rule",
            rule_id=graph.rule,
            rule_version=1,
            desired_paused=True,
            created_at=NOW,
            rule_snapshot={
                "rule_id": str(graph.rule),
                "folder_id": str(graph.folder),
                "version": 1,
                "timezone": "Europe/Moscow",
                "days_of_week": [5],
                "start_time": "23:00",
                "end_time": "02:00",
                "enabled": True,
            },
        )
        | changes
    )


def applied_values(graph, **changes):
    return (
        execution_values(
            graph,
            status="applied",
            observed_paused=True,
            restart_required=False,
            source_session_id=SESSION,
            sent_at=NOW + timedelta(seconds=1),
            verified_at=NOW + timedelta(seconds=2),
            completed_at=NOW + timedelta(seconds=3),
        )
        | changes
    )


def event_values(graph, **changes):
    return (
        dict(
            node_id=graph.node,
            folder_id=graph.folder,
            kind="source",
            source_session_id=SESSION,
            source_event_id=1,
            event_type="StateChanged",
            occurred_at=NOW,
            payload_summary={"state": "idle"},
        )
        | changes
    )


def rejected(pg, model, values, constraint):
    with pytest.raises(IntegrityError) as error, pg.begin() as connection:
        connection.execute(insert(model).values(**values))
    assert error.value.orig.diag.constraint_name == constraint


def test_migration_roundtrip_and_metadata_agree(pg):
    with pg.begin() as connection:
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        migrate(connection, "downgrade", "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'scc_touch_updated_at'")
            )
            == 0
        )
        migrate(connection)
        migrate(connection)  # Reapplying head is a no-op.
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        assert context.get_current_revision() == "a101_stream_anchor"
        assert compare_metadata(context, Base.metadata) == []


def test_real_readiness_requires_migration_and_recovers(pg):
    database = Database(Settings())
    database.engine = pg
    database.check_ready()
    with pg.begin() as connection:
        migrate(connection, "downgrade", "base")
    try:
        with pytest.raises(DatabaseNotReady, match="schema_not_ready"):
            database.check_ready()
    finally:
        with pg.begin() as connection:
            migrate(connection)
    database.check_ready()
    with TestClient(create_app(settings=Settings(), database=database)) as client:
        assert client.get("/health/ready").status_code == 200


def test_orm_defaults_nulls_and_server_update_timestamp(pg):
    graph = seed(pg)
    with Session(pg) as session:
        folder = session.get(Folder, graph.folder)
        assert folder.present is True
        assert folder.actual_paused is None and folder.need_bytes is None
        node = session.get(ManagedNode, graph.node)
        assert node.api_status == "unknown" and node.last_event_id == 0
        assert node.source_session_id is None
        user = AppUser(login="second-admin", password_hash="test-hash")
        session.add(user)
        session.commit()
        assert user.id is not None and user.created_at.tzinfo is not None
        assert user.is_active is True
        initial = user.updated_at
        # A raw SQL update must also get a server timestamp, without ORM onupdate.
        session.execute(
            text("UPDATE app_users SET is_active = false WHERE id = :id"), {"id": user.id}
        )
        session.commit()
        session.refresh(user)
        assert user.updated_at > initial


def test_concurrent_enabled_rules_leave_exactly_one(pg):
    graph = seed(pg)
    with pg.begin() as connection:
        connection.execute(delete(SyncRule))
    barrier = Barrier(2)

    def attempt():
        try:
            with pg.begin() as connection:
                barrier.wait(timeout=5)
                connection.execute(insert(SyncRule).values(**rule_values(graph)))
            return "accepted"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "uq_sync_rules_active_folder"
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["accepted", "rejected"]
    with pg.begin() as connection:
        assert connection.scalar(select(func.count()).select_from(SyncRule)) == 1
        connection.execute(insert(SyncRule).values(**rule_values(graph, enabled=False)))
        connection.execute(insert(SyncRule).values(**rule_values(graph, enabled=False)))
        assert connection.scalar(select(func.count()).select_from(SyncRule)) == 3


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"days_mask": 0}, "days_mask"),
        ({"days_mask": 128}, "days_mask"),
        ({"version": 0}, "version_positive"),
        ({"timezone": " "}, "timezone_nonempty"),
        ({"start_time": time(2)}, "window_nonempty"),
        ({"start_time": time(23, 0, 1)}, "minute_precision"),
        ({"end_time": time(2, 0, 0, 1)}, "minute_precision"),
    ],
)
def test_rule_invalid_fields_rejected(pg, changes, constraint):
    graph = seed(pg)
    rejected(
        pg, SyncRule, rule_values(graph, enabled=False, **changes), f"ck_sync_rules_{constraint}"
    )


def test_same_event_only_rejected_within_same_node_session(pg):
    graph = seed(pg)
    with pg.begin() as connection:
        connection.execute(insert(SyncEvent).values(**event_values(graph)))
    rejected(pg, SyncEvent, event_values(graph), "uq_sync_events_source_position")
    with pg.begin() as connection:
        connection.execute(
            insert(SyncEvent).values(**event_values(graph, source_session_id="b" * 64))
        )
        connection.execute(
            insert(SyncEvent).values(
                **event_values(graph, node_id=graph.other_node, folder_id=graph.other_folder)
            )
        )
        for kind, event_type in [("gap", "HistoryGap"), ("restart", "SourceRestarted")]:
            connection.execute(
                insert(SyncEvent).values(
                    **event_values(
                        graph,
                        kind=kind,
                        event_type=event_type,
                        source_event_id=None,
                        occurred_at=None,
                        folder_id=None,
                        payload_summary={},
                    )
                )
            )
        assert connection.scalar(select(func.count()).select_from(SyncEvent)) == 5


def test_postgresql_end_of_day_time_is_not_an_hhmm_value(pg):
    graph = seed(pg)
    rejected(
        pg,
        SyncRule,
        rule_values(graph, enabled=False, start_time=text("TIME '24:00'")),
        "ck_sync_rules_minute_precision",
    )


def test_failed_event_batch_rolls_back_cursor_and_all_insertions(pg):
    graph = seed(pg)
    with pg.begin() as connection:
        connection.execute(
            update(ManagedNode)
            .where(ManagedNode.id == graph.node)
            .values(
                source_start_time_raw="2026-10-24T12:00:00.123456789Z",
                source_session_id=SESSION,
                last_event_id=1,
            )
        )
        connection.execute(insert(SyncEvent).values(**event_values(graph)))
    with pytest.raises(IntegrityError), pg.begin() as connection:
        connection.execute(
            update(ManagedNode).where(ManagedNode.id == graph.node).values(last_event_id=2)
        )
        connection.execute(insert(SyncEvent).values(**event_values(graph, source_event_id=2)))
        connection.execute(insert(SyncEvent).values(**event_values(graph)))
    with pg.connect() as connection:
        assert (
            connection.scalar(select(ManagedNode.last_event_id).where(ManagedNode.id == graph.node))
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(SyncEvent)) == 1
        assert (
            connection.scalar(
                select(ManagedNode.source_start_time_raw).where(ManagedNode.id == graph.node)
            )
            == "2026-10-24T12:00:00.123456789Z"
        )


def test_deleting_rule_preserves_execution_snapshot(pg):
    graph = seed(pg)
    values = applied_values(graph)
    with pg.begin() as connection:
        command_id = connection.scalar(
            insert(CommandExecution).values(**values).returning(CommandExecution.id)
        )
        connection.execute(delete(SyncRule).where(SyncRule.id == graph.rule))
    with Session(pg) as session:
        execution = session.get(CommandExecution, command_id)
        assert execution.rule_id is None and execution.rule_version == 1
        assert execution.rule_snapshot == values["rule_snapshot"]
        assert execution.status == "applied"
        assert execution.created_at == NOW


@pytest.mark.parametrize("model", [CommandExecution, SyncEvent, StatusSnapshot])
def test_history_cannot_reference_folder_of_another_node(pg, model):
    graph = seed(pg)
    if model is CommandExecution:
        values = execution_values(graph)
    elif model is SyncEvent:
        values = event_values(graph)
    else:
        values = dict(node_id=graph.node, folder_id=graph.folder, scope="folder", success=True)
    rejected(
        pg,
        model,
        values | {"node_id": graph.other_node},
        f"fk_{model.__tablename__}_folder_id_folders",
    )


def test_unknown_folder_never_allows_nonexistent_node(pg):
    graph = seed(pg)
    rejected(
        pg,
        SyncEvent,
        event_values(graph, node_id=uuid4(), folder_id=None),
        "fk_sync_events_node_id_managed_nodes",
    )
    rejected(
        pg,
        StatusSnapshot,
        dict(node_id=uuid4(), scope="node", success=True),
        "fk_status_snapshots_node_id_managed_nodes",
    )


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"observed_paused": None}, "applied_evidence"),
        ({"observed_paused": False}, "applied_evidence"),
        ({"restart_required": None}, "applied_evidence"),
        ({"restart_required": True}, "applied_evidence"),
        ({"source_session_id": None}, "applied_evidence"),
        ({"sent_at": None}, "applied_evidence"),
        ({"verified_at": None}, "applied_evidence"),
        ({"error_code": "unexpected"}, "applied_evidence"),
        ({"verified_at": NOW}, "time_order"),
        ({"completed_at": NOW}, "time_order"),
        ({"sent_at": NOW - timedelta(seconds=1)}, "time_order"),
    ],
)
def test_applied_requires_complete_ordered_evidence(pg, changes, constraint):
    graph = seed(pg)
    rejected(
        pg,
        CommandExecution,
        applied_values(graph, **changes),
        f"ck_command_executions_{constraint}",
    )


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"completed_at": NOW}, "completion"),
        ({"verified_at": NOW}, "completion"),
        ({"error_code": "unexpected"}, "completion"),
        ({"source": "manual"}, "source_fields"),
        ({"rule_snapshot": None}, "source_fields"),
        ({"rule_snapshot": []}, "source_fields"),
        ({"requested_by": "user"}, "source_fields"),
        ({"rule_version": 0}, "rule_version_positive"),
        ({"status": "success", "completed_at": NOW}, "status"),
        ({"source_session_id": "not-a-sha256"}, "source_session_hash"),
    ],
)
def test_pending_and_source_invariants(pg, changes, constraint):
    graph = seed(pg)
    if changes.get("requested_by") == "user":
        changes = changes | {"requested_by": graph.user}
    rejected(
        pg,
        CommandExecution,
        execution_values(graph, **changes),
        f"ck_command_executions_{constraint}",
    )


@pytest.mark.parametrize("status", ["failed", "unknown", "cancelled"])
def test_terminal_failure_requires_completion_and_reason(pg, status):
    graph = seed(pg)
    values = execution_values(graph, status=status, error_code="test_failure")
    rejected(pg, CommandExecution, values, "ck_command_executions_completion")
    values |= {"completed_at": NOW + timedelta(seconds=3)}
    rejected(
        pg, CommandExecution, values | {"error_code": None}, "ck_command_executions_failure_reason"
    )
    with pg.begin() as connection:
        connection.execute(insert(CommandExecution).values(**values))
    if status == "cancelled":
        rejected(
            pg,
            CommandExecution,
            values | {"sent_at": NOW},
            "ck_command_executions_cancelled_unsent",
        )


def test_manual_idempotency_and_pending_uniqueness(pg):
    graph = seed(pg)
    key = uuid4()
    values = execution_values(
        graph,
        source="manual",
        rule_id=None,
        rule_version=None,
        rule_snapshot=None,
        requested_by=graph.user,
        idempotency_key=key,
        desired_paused=False,
    )
    with pg.begin() as connection:
        connection.execute(insert(CommandExecution).values(**values))
    rejected(pg, CommandExecution, execution_values(graph), "uq_command_executions_pending_folder")
    with pg.begin() as connection:
        connection.execute(
            update(CommandExecution).values(
                status="cancelled", completed_at=NOW, error_code="test_cancelled"
            )
        )
    rejected(pg, CommandExecution, values, "uq_command_executions_manual_key")
    rejected(
        pg,
        CommandExecution,
        values | {"node_id": graph.other_node, "folder_id": graph.other_folder},
        "uq_command_executions_manual_key",
    )
    rejected(
        pg,
        CommandExecution,
        values | {"idempotency_key": uuid4(), "desired_paused": True},
        "ck_command_executions_source_fields",
    )
    with pg.begin() as connection:
        connection.execute(
            insert(CommandExecution).values(**(values | {"idempotency_key": uuid4()}))
        )


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"scope": "node", "folder_id": "folder"}, "scope_fields"),
        ({"scope": "node", "actual_paused": False}, "scope_fields"),
        ({"scope": "folder"}, "scope_fields"),
        ({"success": True, "error_code": "unexpected"}, "result_fields"),
        ({"success": False}, "result_fields"),
        ({"success": False, "error_code": " "}, "result_fields"),
        ({"scope": "folder", "folder_id": "folder", "need_bytes": -1}, "counters_nonnegative"),
    ],
)
def test_snapshot_scope_and_unknown_values(pg, changes, constraint):
    graph = seed(pg)
    if changes.get("folder_id") == "folder":
        changes = changes | {"folder_id": graph.folder}
    rejected(
        pg,
        StatusSnapshot,
        dict(node_id=graph.node, scope="node", success=True) | changes,
        f"ck_status_snapshots_{constraint}",
    )


def test_successful_node_probe_does_not_hide_failed_folder_probe(pg):
    graph = seed(pg)
    with pg.begin() as connection:
        connection.execute(
            insert(StatusSnapshot).values(node_id=graph.node, scope="node", success=True)
        )
        connection.execute(
            insert(StatusSnapshot).values(
                node_id=graph.node,
                folder_id=graph.folder,
                scope="folder",
                success=False,
                error_code="runtime_unavailable",
            )
        )
        rows = connection.execute(
            select(
                StatusSnapshot.scope,
                StatusSnapshot.success,
                StatusSnapshot.actual_paused,
                StatusSnapshot.need_bytes,
            ).order_by(StatusSnapshot.scope)
        ).all()
        assert rows == [("folder", False, None, None), ("node", True, None, None)]


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"source_start_time_raw": "raw-only"}, "source_session_pair"),
        ({"last_event_id": 1}, "source_session_pair"),
        ({"last_event_id": -1}, "cursor_nonnegative"),
        ({"source_start_time_raw": "raw", "source_session_id": "z" * 64}, "source_session_hash"),
        ({"last_checked_at": NOW, "last_seen_at": NOW + timedelta(seconds=1)}, "seen_before_check"),
    ],
)
def test_node_cursor_and_source_session(pg, changes, constraint):
    rejected(
        pg,
        ManagedNode,
        dict(display_name="Demo", connection_ref="node-a") | changes,
        f"ck_managed_nodes_{constraint}",
    )


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"actual_paused": False}, "config_pair"),
        ({"runtime_state": "idle"}, "runtime_pair"),
        ({"need_bytes": -1}, "counters_nonnegative"),
        ({"pull_errors": -1}, "counters_nonnegative"),
    ],
)
def test_folder_observations_are_paired_and_nonnegative(pg, changes, constraint):
    graph = seed(pg)
    rejected(
        pg,
        Folder,
        dict(node_id=graph.node, syncthing_folder_id="second", label="Demo") | changes,
        f"ck_folders_{constraint}",
    )


def test_history_restricts_deleting_nodes_folders_and_requester(pg):
    graph = seed(pg)
    with pg.begin() as connection:
        connection.execute(
            insert(CommandExecution).values(
                **execution_values(
                    graph,
                    source="manual",
                    rule_id=None,
                    rule_version=None,
                    rule_snapshot=None,
                    requested_by=graph.user,
                    idempotency_key=uuid4(),
                    desired_paused=False,
                )
            )
        )
    for model, identity in [
        (ManagedNode, graph.node),
        (Folder, graph.folder),
        (AppUser, graph.user),
    ]:
        with pytest.raises(IntegrityError), pg.begin() as connection:
            connection.execute(delete(model).where(model.id == identity))


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"source_event_id": None}, "source_fields"),
        ({"source_event_id": 0}, "source_fields"),
        ({"occurred_at": None}, "source_fields"),
        ({"payload_summary": {"path": "do-not-store"}}, "payload_allowlist"),
        ({"payload_summary": ["raw-body"]}, "payload_allowlist"),
        ({"kind": "gap", "event_type": "HistoryGap"}, "source_fields"),
    ],
)
def test_event_shape_and_payload_allowlist(pg, changes, constraint):
    graph = seed(pg)
    rejected(pg, SyncEvent, event_values(graph, **changes), f"ck_sync_events_{constraint}")
