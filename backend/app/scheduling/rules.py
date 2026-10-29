"""Rule persistence and DTO conversion for the scheduling API.

The service deliberately owns no HTTP or authentication concerns.  API routes
provide the shared SQLAlchemy session and folder advisory-lock helper from A0.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.dto import Rule, RuleCreate, RuleFields, RulePatch
from app.contracts.errors import AppError
from app.contracts.locks import AdvisoryLocks
from app.contracts.types import days_to_mask, hhmm_to_time, mask_to_days, time_to_hhmm
from app.models import Folder, ManagedNode, SyncRule

_ACTIVE_INDEX = "uq_sync_rules_active_folder"


def _error(status: int, code: str, message: str) -> AppError:
    return AppError(status, code, message)


def _rule_dto(row: SyncRule) -> Rule:
    """Convert database storage (mask/time) into the public contract."""

    return Rule.model_validate(
        {
            "id": row.id,
            "folder_id": row.folder_id,
            "timezone": row.timezone,
            "days_of_week": mask_to_days(row.days_mask),
            "start_time": time_to_hhmm(row.start_time),
            "end_time": time_to_hhmm(row.end_time),
            "enabled": row.enabled,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _fields(row: SyncRule) -> RuleFields:
    return RuleFields(
        timezone=row.timezone,
        days_of_week=mask_to_days(row.days_mask),
        start_time=time_to_hhmm(row.start_time),
        end_time=time_to_hhmm(row.end_time),
        enabled=row.enabled,
    )


def _active_index_violation(error: IntegrityError) -> bool:
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    return constraint == _ACTIVE_INDEX or _ACTIVE_INDEX in str(error.orig)


def _raise_integrity(error: IntegrityError) -> None:
    if _active_index_violation(error):
        raise _error(409, "active_rule_conflict", "An active rule already exists") from None
    raise error


def _folder_for_rule(session: Session, folder_id: UUID, *, require_present: bool) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None or (require_present and not folder.present):
        raise _error(404, "folder_not_found", "Folder was not found")
    if session.get(ManagedNode, folder.node_id) is None:
        # A valid schema cannot normally reach this branch, but do not leak a
        # database invariant as a successful rule operation.
        raise _error(404, "node_not_found", "Node was not found")
    return folder


def _assert_can_enable(session: Session, folder: Folder) -> None:
    node = session.get(ManagedNode, folder.node_id)
    if node is None:
        raise _error(404, "node_not_found", "Node was not found")
    if not node.enabled:
        raise _error(409, "node_disabled", "The node is disabled")
    if not folder.present:
        raise _error(404, "folder_not_found", "Folder was not found")


def create_rule(data: RuleCreate, locks: AdvisoryLocks) -> Rule:
    """Create one rule, serializing active-rule checks per folder."""

    with locks.folder(data.folder_id) as lease:
        with lease.session() as session:
            folder = _folder_for_rule(session, data.folder_id, require_present=True)
            if data.enabled:
                _assert_can_enable(session, folder)
            active = session.scalar(
                select(SyncRule.id).where(
                    SyncRule.folder_id == data.folder_id, SyncRule.enabled.is_(True)
                )
            )
            if active is not None and data.enabled:
                raise _error(409, "active_rule_conflict", "An active rule already exists")
            row = SyncRule(
                id=uuid4(),
                folder_id=data.folder_id,
                timezone=data.timezone,
                days_mask=days_to_mask(data.days_of_week),
                start_time=hhmm_to_time(data.start_time),
                end_time=hhmm_to_time(data.end_time),
                enabled=data.enabled,
                version=1,
            )
            session.add(row)
            try:
                session.flush()
                session.refresh(row)
                result = _rule_dto(row)
                session.commit()
            except IntegrityError as error:
                session.rollback()
                _raise_integrity(error)
            return result


def list_rules(
    session: Session, *, folder_id: UUID | None, limit: int, offset: int
) -> tuple[tuple[Rule, ...], int]:
    """Return a stable page and total from one database snapshot.

    Counting and fetching the page with separate READ COMMITTED statements can
    produce a page whose ``items`` no longer agrees with ``total`` when another
    transaction commits between them.  Fetch the ordered filtered relation
    once, then slice the requested page in memory so both values describe the
    same snapshot, including an empty page beyond the end.
    """

    filters = () if folder_id is None else (SyncRule.folder_id == folder_id,)
    rows = session.scalars(
        select(SyncRule).where(*filters).order_by(SyncRule.created_at.desc(), SyncRule.id.desc())
    ).all()
    total = len(rows)
    page = rows[offset : offset + limit]
    return tuple(_rule_dto(row) for row in page), total


def get_rule(session: Session, rule_id: UUID) -> Rule:
    row = session.get(SyncRule, rule_id)
    if row is None:
        raise _error(404, "rule_not_found", "Rule was not found")
    return _rule_dto(row)


def _rule_folder_id(session: Session, rule_id: UUID) -> UUID:
    folder_id = session.scalar(select(SyncRule.folder_id).where(SyncRule.id == rule_id))
    if folder_id is None:
        raise _error(404, "rule_not_found", "Rule was not found")
    return folder_id


def update_rule(rule_id: UUID, data: RulePatch, locks: AdvisoryLocks) -> Rule:
    """Apply a versioned patch while holding the folder lock."""

    # This lookup is only to locate the lock.  The authoritative row is read
    # again after the lock is acquired, so a concurrent delete cannot be used.
    with locks.engine.connect() as connection:
        with Session(bind=connection, expire_on_commit=False) as lookup:
            folder_id = _rule_folder_id(lookup, rule_id)

    with locks.folder(folder_id) as lease:
        with lease.session() as session:
            row = session.get(SyncRule, rule_id)
            if row is None:
                raise _error(404, "rule_not_found", "Rule was not found")
            if row.version != data.expected_version:
                raise _error(409, "version_conflict", "Rule version is stale")
            current = _fields(row)
            changes = data.changes()
            # RulePatch validates a single submitted time, but only the complete
            # merged rule can reveal equal boundaries when the other endpoint is
            # read from storage. Convert that business error into the public API
            # response instead of leaking a Pydantic ValidationError as HTTP 500.
            if changes.get("start_time", current.start_time) == changes.get(
                "end_time", current.end_time
            ):
                raise _error(422, "invalid_window", "Window boundaries must differ")
            merged = data.apply_to(current)
            folder = _folder_for_rule(session, row.folder_id, require_present=False)
            if merged == current:
                return _rule_dto(row)
            # A disabled node blocks only a transition into an active rule.
            # Editing an already-active rule must remain possible so an admin
            # can correct its window before re-enabling the node.
            if merged.enabled and not current.enabled:
                _assert_can_enable(session, folder)
            row.timezone = merged.timezone
            row.days_mask = days_to_mask(merged.days_of_week)
            row.start_time = hhmm_to_time(merged.start_time)
            row.end_time = hhmm_to_time(merged.end_time)
            row.enabled = merged.enabled
            row.version += 1
            try:
                session.flush()
                session.refresh(row)
                result = _rule_dto(row)
                session.commit()
            except IntegrityError as error:
                session.rollback()
                _raise_integrity(error)
            return result


def delete_rule(rule_id: UUID, expected_version: int, locks: AdvisoryLocks) -> None:
    """Delete only the version the caller read; history keeps its snapshot."""

    with locks.engine.connect() as connection:
        with Session(bind=connection, expire_on_commit=False) as lookup:
            folder_id = _rule_folder_id(lookup, rule_id)

    with locks.folder(folder_id) as lease:
        with lease.session() as session:
            row = session.get(SyncRule, rule_id)
            if row is None:
                raise _error(404, "rule_not_found", "Rule was not found")
            if row.version != expected_version:
                raise _error(409, "version_conflict", "Rule version is stale")
            session.delete(row)
            session.commit()
