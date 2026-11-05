"""Recovery and ownership checks against real PostgreSQL locks/constraints."""

from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.config import Settings
from app.contracts.dto import RulePatch
from app.contracts.locks import AdvisoryLocks, LockBusy, LockLost
from app.db import Database
from app.models import CommandExecution, SyncRule
from app.reconciliation.service import ReconciliationService
from app.scheduling.rules import update_rule
from sqlalchemy import insert, select, text
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)
SESSION = "a" * 64


class Adapter:
    def __init__(self, *, paused=True):
        self.paused = paused
        self.writes = []
        self.on_read = lambda: None
        self.on_write = lambda: None
        self.on_confirm = lambda: None

    def read_system(self):
        return SimpleNamespace(source_session_id=SESSION)

    def read_folder(self, _folder_id):
        if self.writes:
            self.on_confirm()
        else:
            self.on_read()
        return SimpleNamespace(paused=self.paused)

    def set_paused(self, folder_id, paused):
        self.on_write()
        self.writes.append((folder_id, paused))
        self.paused = paused

    def restart_required(self):
        return False


@pytest.fixture
def worker(postgres_engine, graph):
    database = Database(Settings())
    database.engine = postgres_engine
    database._sessions = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    locks = AdvisoryLocks(postgres_engine)
    adapter = Adapter()
    service = ReconciliationService(
        database,
        SimpleNamespace(for_node=lambda _node: adapter),
        SimpleNamespace(now_utc=lambda: NOW),
        locks,
    )
    return service, adapter, locks


def add_intent(engine, graph, *, sent=False):
    admin_id, node_id, folder_id = graph
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(CommandExecution).values(
                id=identifier,
                node_id=node_id,
                folder_id=folder_id,
                source="manual",
                requested_by=admin_id,
                idempotency_key=uuid4(),
                desired_paused=False,
                status="pending",
                created_at=NOW - timedelta(seconds=2),
                sent_at=NOW - timedelta(seconds=1) if sent else None,
                source_session_id=SESSION if sent else None,
            )
        )
    return identifier


def add_rule(engine, graph):
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(SyncRule).values(
                id=identifier,
                folder_id=graph[2],
                days_mask=127,
                start_time=time(9),
                end_time=time(17),
                timezone="UTC",
                enabled=True,
                version=1,
            )
        )
    return identifier


@pytest.mark.parametrize("paused", [False, True])
def test_sent_manual_recovers_without_replaying_or_overwriting_history(
    worker, graph, postgres_engine, paused
):
    service, adapter, _locks = worker
    identifier = add_intent(postgres_engine, graph, sent=True)
    adapter.paused = paused
    # Recovery must precede adapter resolution, regardless of observed state.
    service.adapters = SimpleNamespace(
        for_node=lambda _node: pytest.fail("Recovery must not contact the source")
    )

    assert service.run_once() == 1
    assert service.run_once() == 0
    assert adapter.writes == []
    with service.database.session() as session:
        row = session.get(CommandExecution, identifier)
        assert row.status == "unknown"
        assert row.error_code == "worker_interrupted"
        assert row.sent_at == NOW - timedelta(seconds=1)
        assert row.source_session_id == SESSION
        assert row.verified_at is None
        assert row.completed_at == NOW


@pytest.mark.parametrize("source", ["manual", "rule"])
def test_lost_singleton_during_read_prevents_external_write(worker, graph, postgres_engine, source):
    service, adapter, locks = worker
    if source == "manual":
        add_intent(postgres_engine, graph)
    else:
        add_rule(postgres_engine, graph)
    with pytest.raises(LockLost), locks.worker() as singleton:
        service.ensure_active = singleton.ensure_held

        def terminate_singleton():
            with postgres_engine.begin() as killer:
                assert (
                    killer.scalar(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": singleton.backend_pid}
                    )
                    is True
                )

        adapter.on_read = terminate_singleton
        service.run_once()

    assert adapter.writes == []
    # sent_at is conservative intent, never proof that an HTTP write occurred.
    with service.database.session() as session:
        row = session.scalar(select(CommandExecution))
        assert row.status == "pending"
        assert row.verified_at is None


@pytest.mark.parametrize("source", ["manual", "rule"])
def test_singleton_lost_during_write_leaves_recovery_for_the_next_worker(
    worker, graph, postgres_engine, source
):
    service, adapter, locks = worker
    if source == "manual":
        add_intent(postgres_engine, graph)
    else:
        add_rule(postgres_engine, graph)
    with pytest.raises(LockLost), locks.worker() as singleton:
        service.ensure_active = singleton.ensure_held

        def terminate_singleton():
            with postgres_engine.begin() as killer:
                killer.execute(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": singleton.backend_pid}
                )

        adapter.on_write = terminate_singleton
        service.run_once()

    assert adapter.writes == [("shared", False)]
    with service.database.session() as session:
        row = session.scalar(select(CommandExecution))
        assert row.status == "pending"
        assert row.verified_at is None
    with locks.worker() as successor:
        service.ensure_active = successor.ensure_held
        assert service.run_once() == 1
    assert adapter.writes == [("shared", False)]
    with service.database.session() as session:
        row = session.scalar(select(CommandExecution))
        assert row.status == "unknown"
        assert row.error_code == "worker_interrupted"
        assert row.sent_at == NOW


@pytest.mark.parametrize("source", ["manual", "rule"])
def test_folder_lock_serializes_write_and_allows_update_during_confirmation(
    worker, graph, postgres_engine, source
):
    service, adapter, locks = worker
    if source == "manual":
        add_intent(postgres_engine, graph)
        rule_id = None
    else:
        rule_id = add_rule(postgres_engine, graph)
    folder_id = graph[2]
    checks = []

    def check_write_is_serialized():
        with pytest.raises(LockBusy), locks.folder(folder_id, timeout_seconds=0):
            pytest.fail("A rule writer acquired the folder before the external write")
        checks.append("locked_at_write")

    def update_after_write():
        if rule_id is None:
            with locks.folder(folder_id, timeout_seconds=0):
                checks.append("released_for_observation")
        else:
            update_rule(rule_id, RulePatch(expected_version=1, enabled=False), locks)
            checks.append("disabled_during_observation")

    adapter.on_write = check_write_is_serialized
    adapter.on_confirm = update_after_write
    assert service.run_once() == 1
    assert adapter.writes == [("shared", False)]
    assert len(checks) == 2
    with service.database.session() as session:
        row = session.scalar(select(CommandExecution))
        assert row.sent_at == NOW
        if source == "manual":
            assert row.status == "applied"
        else:
            assert row.status == "unknown"
            assert row.error_code == "rule_changed_after_send"
            assert row.verified_at is None


def test_lost_folder_lease_after_sent_commit_prevents_external_write(
    worker, graph, postgres_engine
):
    service, adapter, locks = worker
    add_intent(postgres_engine, graph)

    class LostAtWriteLocks:
        @contextmanager
        def folder(self, folder_id):
            with locks.folder(folder_id) as lease:
                original = lease.ensure_held
                calls = 0

                def kill_before_send_check():
                    nonlocal calls
                    calls += 1
                    # First check opens lease.session; the next is at send.
                    if calls == 2:
                        with postgres_engine.begin() as killer:
                            killer.execute(
                                text("SELECT pg_terminate_backend(:pid)"),
                                {"pid": lease.backend_pid},
                            )
                    original()

                lease.ensure_held = kill_before_send_check
                yield lease

    service.folder_locks = LostAtWriteLocks()
    with pytest.raises(LockLost):
        service.run_once()
    assert adapter.writes == []
    with service.database.session() as session:
        row = session.scalar(select(CommandExecution))
        assert row.status == "pending"
        assert row.sent_at == NOW
        assert row.verified_at is None
