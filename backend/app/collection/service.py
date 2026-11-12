"""One bounded event attempt with an owned factory; no scheduler or status probes yet."""

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from app.collection.store import CollectionDatabaseError, CollectionResult, EventStore
from app.config import Settings
from app.contracts.interfaces import AdapterError, AdapterFactory, Clock
from app.db import Database
from app.registry.resolver import NodeResolver
from app.syncthing_adapter import HttpAdapterFactory


class EventCollector:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Clock,
        *,
        factory_provider: Callable[[], AdapterFactory] | None = None,
    ):
        self.store = EventStore(settings, database, clock)
        self.factory_provider = factory_provider or (
            lambda: HttpAdapterFactory(NodeResolver(settings, database), clock=clock)
        )

    def collect(self, node_id: UUID) -> CollectionResult:
        factory = None
        result = CollectionResult(node_id, "error", error_code="internal_error")
        try:
            point = self.store.load(node_id)
            if point is None:
                return CollectionResult(node_id, "skipped")
            factory = self.factory_provider()
            client = factory.for_node(node_id)
            try:
                batch = client.read_events(point.cursor, timeout_seconds=0)
            except AdapterError as error:
                if error.code != "session_changed":
                    raise
                system = client.read_system()
                if system.device_id != point.target.expected_device_id:
                    raise AdapterError("identity_mismatch") from None
                batch = client.read_events(0, timeout_seconds=0)
                if (
                    batch.source_session_id != system.source_session_id
                    or batch.source_checkpoint.start_time_raw != system.start_time_raw
                ):
                    raise AdapterError("session_changed") from None
            result = self.store.save(point, batch)
        except CollectionDatabaseError:
            result = CollectionResult(node_id, "error", error_code="database_unavailable")
        except AdapterError as error:
            result = CollectionResult(node_id, "error", error_code=error.code.value)
        except Exception:
            result = CollectionResult(node_id, "error", error_code="internal_error")
        finally:
            if factory is not None:
                try:
                    factory.close()
                except Exception:
                    result = replace(result, outcome="error", error_code="resource_close_failed")
        return result
