"""Read-only history with matching filters/count/page from one PostgreSQL snapshot."""

from contextlib import contextmanager
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.contracts import dto
from app.contracts.errors import AppError
from app.contracts.types import DTO, UtcDateTime
from app.db import Database, DatabaseNotReady
from app.models import StatusSnapshot, SyncEvent


class HistoryFilter(DTO):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    from_at: UtcDateTime | None = None
    to_at: UtcDateTime | None = None
    node_id: UUID | None = None
    folder_id: UUID | None = None
    scope: Literal["node", "folder"] | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.from_at is not None and self.to_at is not None and self.from_at >= self.to_at:
            raise AppError(422, "invalid_period", "Invalid history period")
        return self


class HistoryService:
    def __init__(self, database: Database):
        self.database = database

    @contextmanager
    def _session(self):
        try:
            with self.database.session() as session:
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                yield session
        except (DatabaseNotReady, SQLAlchemyError, ValidationError):
            raise AppError(503, "service_unavailable", "History is unavailable") from None

    @staticmethod
    def _where(model, at, filters: HistoryFilter):
        conditions = []
        for name in ("node_id", "folder_id"):
            value = getattr(filters, name)
            if value is not None:
                conditions.append(getattr(model, name) == value)
        if filters.from_at is not None:
            conditions.append(at >= filters.from_at)
        if filters.to_at is not None:
            conditions.append(at < filters.to_at)
        if model is StatusSnapshot and filters.scope is not None:
            conditions.append(model.scope == filters.scope)
        return conditions

    def _page(self, model, at, filters, item_type, page_type):
        conditions = self._where(model, at, filters)
        with self._session() as session:
            total = session.scalar(select(func.count()).select_from(model).where(*conditions))
            rows = session.scalars(
                select(model)
                .where(*conditions)
                .order_by(at.desc(), model.id.desc())
                .limit(filters.limit)
                .offset(filters.offset)
            ).all()
            return page_type(
                items=[item_type.model_validate(row) for row in rows],
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

    def events(self, filters: HistoryFilter) -> dto.EventPage:
        return self._page(SyncEvent, SyncEvent.received_at, filters, dto.Event, dto.EventPage)

    def snapshots(self, filters: HistoryFilter) -> dto.SnapshotPage:
        return self._page(
            StatusSnapshot, StatusSnapshot.observed_at, filters, dto.Snapshot, dto.SnapshotPage
        )
