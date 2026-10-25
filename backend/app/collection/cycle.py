"""Explicit sequential collection pass; A4 owns scheduling and singleton lifetime."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.collection.observations import ObservationCollector, ObservationResult
from app.collection.service import EventCollector
from app.collection.store import CollectionResult
from app.models import ManagedNode, StatusSnapshot


@dataclass(frozen=True)
class CycleResult:
    node_id: UUID
    events: CollectionResult
    observations: ObservationResult


class CollectionService:
    def __init__(self, settings, database, clock, *, factory_provider=None):
        self.settings, self.database, self.clock = settings, database, clock
        self.events = EventCollector(settings, database, clock, factory_provider=factory_provider)
        self.observations = ObservationCollector(
            settings, database, clock, factory_provider=factory_provider
        )

    def collect_node(self, node_id: UUID) -> CycleResult:
        events = self.events.collect(node_id)
        # Every event outcome still permits an independent real status measurement.
        return CycleResult(node_id, events, self.observations.observe(node_id))

    def collect_once(self, *, force=False) -> tuple[CycleResult, ...]:
        now = self.clock.now_utc()
        last = (
            select(StatusSnapshot.node_id, func.max(StatusSnapshot.observed_at).label("at"))
            .where(StatusSnapshot.scope == "node")
            .group_by(StatusSnapshot.node_id)
            .subquery()
        )
        with self.events.store._session() as session:
            points = session.execute(
                select(ManagedNode.id, last.c.at)
                .outerjoin(last, last.c.node_id == ManagedNode.id)
                .where(ManagedNode.enabled.is_(True))
                .order_by(ManagedNode.id)
            ).all()
        due = [
            node
            for node, at in points
            if force
            or at is None
            or (now - at).total_seconds() < 0
            or (now - at).total_seconds() >= self.settings.observation_interval_seconds
        ]
        return tuple(self.collect_node(node) for node in due)
