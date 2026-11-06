"""Authenticated CRUD routes for scheduling rules."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_folder_locks, get_session
from app.contracts.dto import Rule, RuleCreate, RulePage, RulePatch
from app.contracts.locks import AdvisoryLocks
from app.scheduling.rules import (
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_rule,
)

router = APIRouter(prefix="/rules", tags=["rules"])
SessionDep = Annotated[Session, Depends(get_session)]
LocksDep = Annotated[AdvisoryLocks, Depends(get_folder_locks)]


@router.get("", response_model=RulePage)
def read_rules(
    session: SessionDep,
    folder_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> RulePage:
    items, total = list_rules(session, folder_id=folder_id, limit=limit, offset=offset)
    return RulePage(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=Rule, status_code=status.HTTP_201_CREATED)
def add_rule(data: RuleCreate, locks: LocksDep) -> Rule:
    return create_rule(data, locks)


@router.get("/{rule_id}", response_model=Rule)
def read_rule(rule_id: UUID, session: SessionDep) -> Rule:
    return get_rule(session, rule_id)


@router.patch("/{rule_id}", response_model=Rule)
def patch_rule(
    rule_id: UUID,
    data: RulePatch,
    locks: LocksDep,
) -> Rule:
    return update_rule(rule_id, data, locks)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rule(
    rule_id: UUID,
    locks: LocksDep,
    expected_version: int = Query(..., ge=1),
) -> Response:
    delete_rule(rule_id, expected_version, locks)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
