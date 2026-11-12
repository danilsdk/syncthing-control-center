"""A3 history router; compose through create_app(routers=(router,)) under require_admin."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.nodes import Limit, Offset
from app.collection.history import HistoryFilter, HistoryService
from app.contracts import dto
from app.contracts.types import UtcDateTime

router = APIRouter()


def get_history(request: Request) -> HistoryService:
    return HistoryService(request.app.state.database)


def history_filter(
    limit: Limit = 50,
    offset: Offset = 0,
    from_at: Annotated[UtcDateTime, Query(alias="from")] = None,
    to_at: Annotated[UtcDateTime, Query(alias="to")] = None,
    node_id: UUID = None,
    folder_id: UUID = None,
) -> HistoryFilter:
    return HistoryFilter(
        limit=limit,
        offset=offset,
        from_at=from_at,
        to_at=to_at,
        node_id=node_id,
        folder_id=folder_id,
    )


History = Annotated[HistoryService, Depends(get_history)]
Filters = Annotated[HistoryFilter, Depends(history_filter)]


@router.get(
    "/events",
    response_model=dto.EventPage,
    operation_id="list_events",
    responses={code: {"model": dto.Error} for code in (401, 422, 503)},
)
def events(service: History, filters: Filters):
    return service.events(filters)


@router.get(
    "/snapshots",
    response_model=dto.SnapshotPage,
    operation_id="list_snapshots",
    responses={code: {"model": dto.Error} for code in (401, 422, 503)},
)
def snapshots(service: History, filters: Filters, scope: Literal["node", "folder"] = None):
    return service.snapshots(filters.model_copy(update={"scope": scope}))
