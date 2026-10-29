from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.contracts.interfaces import AdapterError
from app.contracts.types import ExecutionStatus
from app.models import CommandExecution, SyncRule
from app.reconciliation.decisions import Confirmation
from app.reconciliation.service import ReconciliationService, _Target

SESSION = "a" * 64


class FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 10, 29, 12, tzinfo=UTC)


class FakeAdapter:
    def __init__(
        self, *, paused: bool, write_error: AdapterError | None = None
    ) -> None:
        self.paused = paused
        self.write_error = write_error
        self.writes: list[tuple[str, bool]] = []

    def set_paused(self, folder_id: str, paused: bool) -> None:
        self.writes.append((folder_id, paused))
        if self.write_error is not None:
            raise self.write_error

    def read_system(self):
        return SimpleNamespace(source_session_id=SESSION)

    def read_folder(self, folder_id: str):
        return SimpleNamespace(paused=self.paused)

    def restart_required(self) -> bool:
        return False


def target(adapter: FakeAdapter) -> _Target:
    return _Target(
        execution_id=uuid4(),
        folder_id=uuid4(),
        node_id=uuid4(),
        external_folder_id="shared",
        desired_paused=True,
        source_session_id=SESSION,
        adapter=adapter,
        rule_id=uuid4(),
        rule_version=1,
        rule_snapshot={},
    )


def test_worker_records_applied_only_after_post_write_read() -> None:
    adapter = FakeAdapter(paused=True)
    service = ReconciliationService(None, None, FixedClock(), None)  # type: ignore[arg-type]
    captured = {}
    service._finish = lambda *args, **kwargs: captured.update(outcome=args[1], **kwargs)  # type: ignore[method-assign]

    service._send_and_confirm(target(adapter))

    assert adapter.writes == [("shared", True)]
    assert captured["outcome"].status is ExecutionStatus.APPLIED
    assert captured["outcome"].verified is True


def test_unknown_write_can_finish_applied_after_confirmation() -> None:
    adapter = FakeAdapter(
        paused=True,
        write_error=AdapterError("timeout", operation="write", write_outcome="unknown"),
    )
    service = ReconciliationService(None, None, FixedClock(), None)  # type: ignore[arg-type]
    captured = {}
    service._finish = lambda *args, **kwargs: captured.update(outcome=args[1], **kwargs)  # type: ignore[method-assign]

    service._send_and_confirm(target(adapter))

    assert captured["outcome"].status is ExecutionStatus.APPLIED


class FakeSession:
    def __init__(self, row) -> None:
        self.row = row
        self.commits = 0

    def get(self, _model, _id):
        return self.row

    def commit(self) -> None:
        self.commits += 1


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    @contextmanager
    def session(self):
        yield self._session


class FakeLease:
    def __init__(self, session) -> None:
        self._session = session

    @contextmanager
    def session(self):
        yield self._session


class FakeFolderLocks:
    def __init__(self, session) -> None:
        self._lease = FakeLease(session)

    @contextmanager
    def folder(self, _folder_id):
        yield self._lease


class FinishSession:
    def __init__(self, target, *, changed: bool) -> None:
        self.execution = SimpleNamespace(
            status=ExecutionStatus.PENDING,
            error_code=None,
            observed_paused=None,
            restart_required=None,
            source_session_id=None,
            verified_at=None,
            completed_at=None,
        )
        self.rule = SimpleNamespace(
            version=target.rule_version + (1 if changed else 0),
            enabled=not changed,
        )
        self.commits = 0

    def get(self, model, _id):
        return (
            self.execution
            if model is CommandExecution
            else self.rule
            if model is SyncRule
            else None
        )

    def commit(self) -> None:
        self.commits += 1


def test_rule_change_after_send_invalidates_confirmation() -> None:
    adapter = FakeAdapter(paused=True)
    target_row = target(adapter)
    session = FinishSession(target_row, changed=True)
    service = ReconciliationService(
        None,
        None,
        FixedClock(),
        FakeFolderLocks(session),  # type: ignore[arg-type]
    )

    service._finish(
        target_row,
        Confirmation(ExecutionStatus.APPLIED, None, True),
        observed_paused=True,
        restart_required=False,
        source_session_id=SESSION,
    )

    assert session.execution.status is ExecutionStatus.UNKNOWN
    assert session.execution.error_code == "rule_changed_after_send"
    assert session.execution.verified_at is None


def test_sent_pending_execution_is_not_replayed_after_restart() -> None:
    row = SimpleNamespace(
        status=ExecutionStatus.PENDING,
        sent_at=FixedClock().now_utc(),
        error_code=None,
        completed_at=None,
    )
    session = FakeSession(row)
    service = ReconciliationService(FakeDatabase(session), None, FixedClock(), None)  # type: ignore[arg-type]

    assert service._recover_rule_pending(uuid4()) is True
    assert row.status is ExecutionStatus.UNKNOWN
    assert row.error_code == "worker_interrupted"
    assert session.commits == 1


def test_unsent_pending_execution_is_cancelled_for_fresh_recalculation() -> None:
    row = SimpleNamespace(
        status=ExecutionStatus.PENDING,
        sent_at=None,
        error_code=None,
        completed_at=None,
    )
    session = FakeSession(row)
    service = ReconciliationService(FakeDatabase(session), None, FixedClock(), None)  # type: ignore[arg-type]

    assert service._recover_rule_pending(uuid4()) is True
    assert row.status is ExecutionStatus.CANCELLED
    assert row.error_code == "stale_intent"
