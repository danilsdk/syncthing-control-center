"""Single-worker reconciliation of desired folder state.

The service separates intent, send and observation.  It never calls Syncthing
directly: all source operations go through the injected ``AdapterFactory``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.contracts.dto import Execution, RuleFields, RuleSnapshot
from app.contracts.interfaces import AdapterError, AdapterFactory, Clock, SyncthingAdapter
from app.contracts.locks import AdvisoryLocks
from app.contracts.types import ExecutionStatus, mask_to_days, time_to_hhmm
from app.db import Database
from app.models import CommandExecution, Folder, ManagedNode, SyncRule
from app.scheduling.engine import calculate_desired_paused
from sqlalchemy import exists, select

from .decisions import Confirmation, classify_confirmation


@dataclass(frozen=True)
class _Target:
    execution_id: UUID | None
    folder_id: UUID
    node_id: UUID
    external_folder_id: str
    desired_paused: bool
    source_session_id: str
    adapter: SyncthingAdapter
    rule_id: UUID | None
    rule_version: int | None
    rule_snapshot: dict | None


def _rule_fields(row: SyncRule) -> RuleFields:
    return RuleFields(
        timezone=row.timezone,
        days_of_week=mask_to_days(row.days_mask),
        start_time=time_to_hhmm(row.start_time),
        end_time=time_to_hhmm(row.end_time),
        enabled=row.enabled,
    )


def _snapshot(row: SyncRule) -> RuleSnapshot:
    return RuleSnapshot(
        rule_id=row.id,
        version=row.version,
        folder_id=row.folder_id,
        **_rule_fields(row).model_dump(),
    )


def execution_to_dto(row: CommandExecution) -> Execution:
    snapshot = RuleSnapshot.model_validate(row.rule_snapshot) if row.rule_snapshot else None
    return Execution.model_validate(
        {
            "id": row.id,
            "node_id": row.node_id,
            "folder_id": row.folder_id,
            "source": row.source,
            "rule_id": row.rule_id,
            "rule_version": row.rule_version,
            "rule_snapshot": snapshot,
            "desired_paused": row.desired_paused,
            "status": row.status,
            "observed_paused": row.observed_paused,
            "restart_required": row.restart_required,
            "source_session_id": row.source_session_id,
            "created_at": row.created_at,
            "sent_at": row.sent_at,
            "verified_at": row.verified_at,
            "completed_at": row.completed_at,
            "error_code": row.error_code,
        }
    )


class ReconciliationService:
    """One bounded reconciliation pass.

    ``run_once`` is safe to call from the A0 singleton worker.  It does not run
    from API requests, so a web process cannot accidentally create a second
    executor.
    """

    def __init__(
        self,
        database: Database,
        adapters: AdapterFactory,
        clock: Clock,
        folder_locks: AdvisoryLocks,
        ensure_active: Callable[[], None] | None = None,
    ) -> None:
        self.database = database
        self.adapters = adapters
        self.clock = clock
        self.folder_locks = folder_locks
        self.ensure_active = ensure_active or (lambda: None)

    def run_once(self) -> int:
        processed = 0
        for execution_id, source in self._pending_ids():
            self.ensure_active()
            if source == "manual":
                processed += int(self._process_manual(execution_id))
            else:
                processed += int(self._recover_rule_pending(execution_id))
        for folder_id, rule_id in self._active_rule_ids():
            self.ensure_active()
            processed += int(self._process_rule(folder_id, rule_id))
        return processed

    def _pending_ids(self) -> list[tuple[UUID, str]]:
        with self.database.session() as session:
            return list(
                session.execute(
                    select(CommandExecution.id, CommandExecution.source)
                    .where(CommandExecution.status == ExecutionStatus.PENDING)
                    .order_by(CommandExecution.created_at, CommandExecution.id)
                ).all()
            )

    def _active_rule_ids(self) -> list[tuple[UUID, UUID]]:
        with self.database.session() as session:
            return list(
                session.execute(
                    select(SyncRule.folder_id, SyncRule.id)
                    .join(Folder, Folder.id == SyncRule.folder_id)
                    .join(ManagedNode, ManagedNode.id == Folder.node_id)
                    .where(
                        SyncRule.enabled.is_(True),
                        Folder.present.is_(True),
                        ManagedNode.enabled.is_(True),
                        ~exists(
                            select(CommandExecution.id).where(
                                CommandExecution.folder_id == SyncRule.folder_id,
                                CommandExecution.status == ExecutionStatus.PENDING,
                            )
                        ),
                    )
                ).all()
            )

    def _recover_rule_pending(self, execution_id: UUID) -> bool:
        """Do not replay an already-sent command after a worker restart."""

        with self.database.session() as session:
            row = session.get(CommandExecution, execution_id)
            if row is None or row.status != ExecutionStatus.PENDING:
                return False
            if row.sent_at is None:
                # A never-sent intent is stale after a new pass; the active rule
                # query will calculate a fresh target from the current clock.
                row.status = ExecutionStatus.CANCELLED
                row.error_code = "stale_intent"
            else:
                row.status = ExecutionStatus.UNKNOWN
                row.error_code = "worker_interrupted"
            row.completed_at = self.clock.now_utc()
            session.commit()
        return True

    def _process_rule(self, folder_id: UUID, rule_id: UUID) -> bool:
        with self.folder_locks.folder(folder_id) as lease:
            with lease.session() as session:
                rule = session.get(SyncRule, rule_id)
                folder = session.get(Folder, folder_id)
                if rule is None or folder is None or not rule.enabled or not folder.present:
                    return False
                node = session.get(ManagedNode, folder.node_id)
                if node is None or not node.enabled:
                    return False
                try:
                    adapter = self.adapters.for_node(node.id)
                except AdapterError:
                    # A resolver failure is an unavailable observation, not a
                    # worker-fatal exception. A3 records the probe failure and
                    # the next pass retries after recalculating the rule.
                    return False
                try:
                    system = adapter.read_system()
                    observed = adapter.read_folder(folder.syncthing_folder_id)
                    desired = calculate_desired_paused(_rule_fields(rule), self.clock.now_utc())
                except (AdapterError, ValueError):
                    return False
                if desired is None or observed.paused == desired:
                    return False
                snapshot = _snapshot(rule)
                row = CommandExecution(
                    id=uuid4(),
                    node_id=node.id,
                    folder_id=folder.id,
                    source="rule",
                    rule_id=rule.id,
                    rule_version=rule.version,
                    rule_snapshot=snapshot.model_dump(mode="json"),
                    desired_paused=desired,
                    status=ExecutionStatus.PENDING,
                    created_at=self.clock.now_utc(),
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                session.commit()
                row_id = row.id
                target = _Target(
                    row_id,
                    folder.id,
                    node.id,
                    folder.syncthing_folder_id,
                    desired,
                    system.source_session_id,
                    adapter,
                    rule.id,
                    rule.version,
                    snapshot.model_dump(mode="json"),
                )
                row.sent_at = self.clock.now_utc()
                session.flush()
                session.commit()
        self._send_and_confirm(target)
        return True

    def _process_manual(self, execution_id: UUID) -> bool:
        with self.database.session() as lookup:
            pending = lookup.get(CommandExecution, execution_id)
            if pending is None or pending.status != ExecutionStatus.PENDING:
                return False
            folder_id = pending.folder_id
        with self.folder_locks.folder(folder_id) as lease:
            with lease.session() as session:
                row = session.get(CommandExecution, execution_id)
                folder = session.get(Folder, folder_id)
                if row is None or folder is None or row.status != ExecutionStatus.PENDING:
                    return False
                node = session.get(ManagedNode, folder.node_id)
                if node is None or not node.enabled or not folder.present:
                    row.status = ExecutionStatus.UNKNOWN
                    row.error_code = "folder_unavailable"
                    row.completed_at = self.clock.now_utc()
                    session.commit()
                    return True
                active = session.scalar(
                    select(SyncRule.id).where(
                        SyncRule.folder_id == folder.id, SyncRule.enabled.is_(True)
                    )
                )
                if active is not None:
                    row.status = ExecutionStatus.CANCELLED
                    row.error_code = "active_rule"
                    row.completed_at = self.clock.now_utc()
                    session.commit()
                    return True
                try:
                    adapter = self.adapters.for_node(node.id)
                except AdapterError as error:
                    row.status = ExecutionStatus.UNKNOWN
                    row.error_code = error.code.value
                    row.completed_at = self.clock.now_utc()
                    session.commit()
                    return True
                try:
                    system = adapter.read_system()
                    observed = adapter.read_folder(folder.syncthing_folder_id)
                except AdapterError as error:
                    row.status = ExecutionStatus.UNKNOWN
                    row.error_code = error.code.value
                    row.completed_at = self.clock.now_utc()
                    session.commit()
                    return True
                if observed.paused is False:
                    row.status = ExecutionStatus.CANCELLED
                    row.error_code = "already_resumed"
                    row.completed_at = self.clock.now_utc()
                    row.source_session_id = system.source_session_id
                    session.commit()
                    return True
                row.sent_at = self.clock.now_utc()
                row.source_session_id = system.source_session_id
                session.flush()
                session.commit()
                target = _Target(
                    row.id,
                    folder.id,
                    node.id,
                    folder.syncthing_folder_id,
                    False,
                    system.source_session_id,
                    adapter,
                    None,
                    None,
                    None,
                )
        self._send_and_confirm(target)
        return True

    def _send_and_confirm(self, target: _Target) -> None:
        write_error: AdapterError | None = None
        try:
            target.adapter.set_paused(target.external_folder_id, target.desired_paused)
        except AdapterError as error:
            write_error = error
        observed_paused: bool | None = None
        restart_required: bool | None = None
        observed_session: str | None = None
        if write_error is None or write_error.write_outcome == "unknown":
            try:
                observed_session = target.adapter.read_system().source_session_id
                observed_paused = target.adapter.read_folder(target.external_folder_id).paused
                restart_required = target.adapter.restart_required()
            except AdapterError as error:
                if write_error is None:
                    write_error = error
        outcome = classify_confirmation(
            desired_paused=target.desired_paused,
            observed_paused=observed_paused,
            restart_required=restart_required,
            expected_source_session=target.source_session_id,
            observed_source_session=observed_session,
            write_error=write_error,
        )
        self._finish(
            target,
            outcome,
            observed_paused=observed_paused,
            restart_required=restart_required,
            source_session_id=observed_session or target.source_session_id,
        )

    def _finish(
        self,
        target: _Target,
        outcome: Confirmation,
        *,
        observed_paused: bool | None,
        restart_required: bool | None,
        source_session_id: str | None,
    ) -> None:
        # Serialize the final evidence with rule updates. A version change that
        # happened while the external call was in flight invalidates the old
        # confirmation and must be retried from a fresh rule calculation.
        with self.folder_locks.folder(target.folder_id) as lease:
            with lease.session() as session:
                row = session.get(CommandExecution, target.execution_id)
                if row is None or row.status != ExecutionStatus.PENDING:
                    return
                if target.rule_id is not None:
                    rule = session.get(SyncRule, target.rule_id)
                    if rule is None or rule.version != target.rule_version or not rule.enabled:
                        outcome = Confirmation(
                            ExecutionStatus.UNKNOWN,
                            "rule_changed_after_send",
                            False,
                        )
                row.status = outcome.status
                row.error_code = outcome.error_code
                row.observed_paused = observed_paused
                row.restart_required = restart_required
                row.source_session_id = source_session_id
                row.verified_at = self.clock.now_utc() if outcome.verified else None
                row.completed_at = self.clock.now_utc()
                session.commit()


class ReconciliationRuntime:
    """A4-injected blocking worker runtime; API processes never instantiate it."""

    def __init__(self, stop_event: threading.Event | None = None) -> None:
        self.stop_event = stop_event

    def run(self, context) -> None:
        stop = self.stop_event or threading.Event()
        while not stop.is_set():
            context.ensure_active()
            ReconciliationService(
                context.database,
                context.adapters,
                context.clock,
                context.folder_locks,
                context.ensure_active,
            ).run_once()
            stop.wait(context.settings.reconciliation_interval_seconds)


def run_once(context) -> int:
    context.ensure_active()
    return ReconciliationService(
        context.database,
        context.adapters,
        context.clock,
        context.folder_locks,
        context.ensure_active,
    ).run_once()
