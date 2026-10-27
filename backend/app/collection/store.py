"""Canonical event/checkpoint transaction; all transport work belongs outside this module."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.collection.planning import plan_batch
from app.config import Settings
from app.contracts.interfaces import AdapterError, Clock, EventBatch
from app.db import Database, DatabaseNotReady
from app.models import Folder, ManagedNode, SyncEvent
from app.registry.resolver import resolve_record
from app.syncthing_adapter import ResolvedNode


class CollectionDatabaseError(Exception):
    def __init__(self):
        super().__init__("Collection database operation failed")


@dataclass(frozen=True)
class ReadPoint:
    node_id: UUID
    connection_ref: str
    cursor: int
    target: ResolvedNode = field(repr=False)


@dataclass(frozen=True)
class CollectionResult:
    node_id: UUID
    outcome: Literal["stored", "conflict", "deferred", "skipped", "error"]
    events_inserted: int = 0
    markers_inserted: int = 0
    cursor: int | None = None
    reconcile: bool = False
    error_code: str | None = None


class EventStore:
    def __init__(self, settings: Settings, database: Database, clock: Clock):
        self.settings, self.database, self.clock = settings, database, clock

    @contextmanager
    def _session(self):
        try:
            with self.database.session() as session:
                yield session
        except (DatabaseNotReady, SQLAlchemyError):
            # Even an unknown commit outcome is retried from persisted proof, never from memory.
            raise CollectionDatabaseError() from None

    def load(self, node_id: UUID) -> ReadPoint | None:
        with self._session() as session:
            node = session.get(ManagedNode, node_id)
            if node is None or not node.enabled:
                return None
            return ReadPoint(
                node.id,
                node.connection_ref,
                node.last_event_id,
                resolve_record(self.settings, node),
            )

    def save(self, point: ReadPoint, batch: EventBatch) -> CollectionResult:
        plan = plan_batch(point.node_id, point.target.checkpoint, point.cursor, batch)
        if plan.deferred:
            return CollectionResult(point.node_id, "deferred", error_code="session_changed")
        received_at = self.clock.now_utc()
        with self._session() as session, session.begin():
            node = session.scalar(
                select(ManagedNode).where(ManagedNode.id == point.node_id).with_for_update()
            )
            matches = (
                node is not None
                and node.enabled
                and node.connection_ref == point.connection_ref
                and node.last_event_id == point.cursor
            )
            if matches:
                try:
                    matches = resolve_record(self.settings, node) == point.target
                except AdapterError:
                    matches = False
            if not matches:
                return CollectionResult(point.node_id, "conflict")
            mapping = dict(
                session.execute(
                    select(Folder.syncthing_folder_id, Folder.id).where(
                        Folder.node_id == point.node_id
                    )
                ).all()
            )
            sources, markers = [], []
            for row in plan.rows:
                value = {
                    "id": uuid4(),
                    "node_id": point.node_id,
                    "folder_id": mapping.get(row.external_folder_id),
                    "kind": row.kind,
                    "source_session_id": plan.checkpoint.source_session_id,
                    "source_event_id": row.source_event_id,
                    "event_type": row.event_type,
                    "occurred_at": row.occurred_at,
                    "received_at": received_at,
                    "payload_summary": row.payload or {},
                }
                (sources if row.kind == "source" else markers).append(value)
            inserted = 0
            if sources:
                statement = (
                    insert(SyncEvent)
                    .values(sources)
                    .on_conflict_do_nothing(
                        index_elements=[
                            SyncEvent.node_id,
                            SyncEvent.source_session_id,
                            SyncEvent.source_event_id,
                        ],
                        index_where=SyncEvent.kind == "source",
                    )
                    .returning(SyncEvent.id)
                )
                inserted = len(session.scalars(statement).all())
            if markers:
                session.execute(insert(SyncEvent).values(markers))
            node.source_start_time_raw = plan.checkpoint.start_time_raw
            node.source_session_id = plan.checkpoint.source_session_id
            node.source_stream_anchor = plan.checkpoint.anchor.model_dump(mode="json")
            node.last_event_id = plan.cursor
            # The context commits fields and rows together; a flush/commit failure rolls them back.
        return CollectionResult(
            point.node_id, "stored", inserted, len(markers), plan.cursor, plan.reconcile
        )
