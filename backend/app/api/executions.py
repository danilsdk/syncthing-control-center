"""Protected history and manual-resume endpoints for command executions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.api.dependencies import get_folder_locks, get_session
from app.contracts.dto import Execution, ExecutionPage, Resume, User
from app.contracts.errors import AppError
from app.contracts.locks import AdvisoryLocks
from app.contracts.types import ExecutionStatus, UtcDateTime
from app.models import CommandExecution, Folder, ManagedNode, SyncRule
from app.reconciliation.service import execution_to_dto

router = APIRouter(prefix="/executions", tags=["executions"])
SessionDep = Annotated[Session, Depends(get_session)]
LocksDep = Annotated[AdvisoryLocks, Depends(get_folder_locks)]
AdminDep = Annotated[User, Depends(require_admin)]
FromParam = Annotated[UtcDateTime | None, Query(alias="from")]


def _not_found(code: str, message: str) -> AppError:
    return AppError(404, code, message)


@router.get("", response_model=ExecutionPage)
def list_executions(
    session: SessionDep,
    from_: FromParam = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    to: UtcDateTime | None = None,
    node_id: UUID | None = None,
    folder_id: UUID | None = None,
    source: Literal["rule", "manual"] | None = None,
    status: ExecutionStatus | None = None,
) -> ExecutionPage:
    filters = []
    if from_ is not None:
        filters.append(CommandExecution.created_at >= from_)
    if to is not None:
        filters.append(CommandExecution.created_at < to)
    if from_ is not None and to is not None and from_ >= to:
        raise AppError(422, "invalid_period", "Execution period must be positive")
    if node_id is not None:
        filters.append(CommandExecution.node_id == node_id)
    if folder_id is not None:
        filters.append(CommandExecution.folder_id == folder_id)
    if source is not None:
        filters.append(CommandExecution.source == source)
    if status is not None:
        filters.append(CommandExecution.status == status)
    total = int(
        session.scalar(select(func.count()).select_from(CommandExecution).where(*filters)) or 0
    )
    rows = session.scalars(
        select(CommandExecution)
        .where(*filters)
        .order_by(CommandExecution.created_at.desc(), CommandExecution.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ExecutionPage(
        items=tuple(execution_to_dto(row) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/resume", response_model=Execution, status_code=status.HTTP_202_ACCEPTED)
def resume_folder(
    data: Resume,
    response: Response,
    admin: AdminDep,
    locks: LocksDep,
) -> Execution:
    """Create an idempotent resume intent; the singleton worker sends it later."""

    with locks.folder(data.folder_id) as lease:
        with lease.session() as session:
            existing = session.scalar(
                select(CommandExecution).where(
                    CommandExecution.requested_by == admin.id,
                    CommandExecution.idempotency_key == data.idempotency_key,
                )
            )
            if existing is not None:
                if existing.folder_id != data.folder_id:
                    raise AppError(
                        409,
                        "idempotency_conflict",
                        "Idempotency key belongs to another folder",
                    )
                response.status_code = status.HTTP_200_OK
                return execution_to_dto(existing)
            folder = session.get(Folder, data.folder_id)
            if folder is None or not folder.present:
                raise _not_found("folder_not_found", "Folder was not found")
            node = session.get(ManagedNode, folder.node_id)
            if node is None:
                raise _not_found("node_not_found", "Node was not found")
            if not node.enabled:
                raise AppError(409, "node_disabled", "The node is disabled")
            active = session.scalar(
                select(SyncRule.id).where(
                    SyncRule.folder_id == data.folder_id, SyncRule.enabled.is_(True)
                )
            )
            if active is not None:
                raise AppError(409, "operation_in_progress", "An active rule controls this folder")
            pending = session.scalar(
                select(CommandExecution.id).where(
                    CommandExecution.folder_id == data.folder_id,
                    CommandExecution.status == ExecutionStatus.PENDING,
                )
            )
            if pending is not None:
                raise AppError(409, "operation_in_progress", "A command is already pending")
            row = CommandExecution(
                id=uuid4(),
                node_id=node.id,
                folder_id=folder.id,
                source="manual",
                requested_by=admin.id,
                idempotency_key=data.idempotency_key,
                desired_paused=False,
                status=ExecutionStatus.PENDING,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            try:
                session.flush()
                session.refresh(row)
                result = execution_to_dto(row)
                session.commit()
            except IntegrityError:
                session.rollback()
                raise AppError(
                    409, "idempotency_conflict", "Idempotency key was already used"
                ) from None
            response.status_code = status.HTTP_202_ACCEPTED
            return result


@router.get("/{execution_id}", response_model=Execution)
def read_execution(execution_id: UUID, session: SessionDep) -> Execution:
    row = session.get(CommandExecution, execution_id)
    if row is None:
        raise _not_found("execution_not_found", "Execution was not found")
    return execution_to_dto(row)
