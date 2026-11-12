"""Actual system/config/runtime probes, separate history and conservative cache updates."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.collection.store import CollectionDatabaseError, EventStore
from app.config import Settings
from app.contracts.interfaces import (
    AdapterError,
    AdapterFactory,
    Clock,
    FolderConfig,
    FolderRuntime,
    NodeConnection,
    SystemObservation,
)
from app.db import Database
from app.models import Folder, ManagedNode, StatusSnapshot
from app.registry.resolver import NodeResolver, node_connection
from app.syncthing_adapter import HttpAdapterFactory


@dataclass(frozen=True)
class ObservationResult:
    node_id: UUID
    outcome: str
    snapshots_inserted: int = 0
    cache_updated: bool = False
    node_error: str | None = None
    config_error: str | None = None
    runtime_failures: int = 0
    error_code: str | None = None


@dataclass(frozen=True)
class ProbePoint:
    node_id: UUID
    reference: str
    expected_id: str
    connection: NodeConnection = field(repr=False)


@dataclass
class Probe:
    node_at: datetime
    finished_at: datetime
    system: SystemObservation | None = None
    node_error: str | None = None
    configs: tuple[FolderConfig, ...] | None = None
    config_error: str | None = None
    runtime: dict[str, FolderRuntime | tuple[datetime, str]] = field(default_factory=dict)


def _validated(model, value):
    try:
        return model.model_validate(value.model_dump())
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise AdapterError("invalid_response") from None


def capture(client, point: ProbePoint, clock: Clock) -> Probe:
    probe = Probe(clock.now_utc(), clock.now_utc())
    try:
        probe.system = _validated(SystemObservation, client.read_system())
        if probe.system.device_id != point.expected_id:
            raise AdapterError("identity_mismatch")
        probe.node_at = probe.system.observed_at
    except AdapterError as error:
        probe.system, probe.node_error = None, error.code.value
        probe.node_at = probe.finished_at = clock.now_utc()
        return probe
    except Exception:
        probe.system, probe.node_error = None, "internal_error"
        probe.node_at = probe.finished_at = clock.now_utc()
        return probe
    try:
        values = client.list_folders()
        if not isinstance(values, (list, tuple)):
            raise AdapterError("invalid_response")
        configs = tuple(_validated(FolderConfig, row) for row in values)
        if len({row.syncthing_folder_id for row in configs}) != len(configs):
            raise AdapterError("invalid_response")
        probe.configs = configs
        for config in configs:
            try:
                probe.runtime[config.syncthing_folder_id] = _validated(
                    FolderRuntime, client.read_runtime(config.syncthing_folder_id)
                )
            except AdapterError as error:
                if error.code in {"identity_mismatch", "session_changed"}:
                    raise
                probe.runtime[config.syncthing_folder_id] = (clock.now_utc(), error.code.value)
            except Exception:
                probe.runtime[config.syncthing_folder_id] = (clock.now_utc(), "internal_error")
        after = _validated(SystemObservation, client.read_system())
        if after.device_id != point.expected_id:
            raise AdapterError("identity_mismatch")
        if after.source_session_id != probe.system.source_session_id:
            raise AdapterError("session_changed")
    except AdapterError as error:
        probe.configs, probe.runtime, probe.config_error = None, {}, error.code.value
    except Exception:
        probe.configs, probe.runtime, probe.config_error = None, {}, "internal_error"
    probe.finished_at = clock.now_utc()
    return probe


class ObservationCollector(EventStore):
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Clock,
        *,
        factory_provider: Callable[[], AdapterFactory] | None = None,
    ):
        super().__init__(settings, database, clock)
        self.factory_provider = factory_provider or (
            lambda: HttpAdapterFactory(NodeResolver(settings, database), clock=clock)
        )

    def _point(self, node_id: UUID) -> ProbePoint | None:
        with self._session() as session:
            node = session.get(ManagedNode, node_id)
            if node is None or not node.enabled:
                return None
            if not node.syncthing_device_id:
                raise AdapterError("identity_mismatch")
            return ProbePoint(
                node_id,
                node.connection_ref,
                node.syncthing_device_id,
                node_connection(self.settings, node.connection_ref, node_id),
            )

    def observe(self, node_id: UUID) -> ObservationResult:
        factory = None
        result = ObservationResult(node_id, "error", error_code="internal_error")
        try:
            point = self._point(node_id)
            if point is None:
                return ObservationResult(node_id, "skipped")
            factory = self.factory_provider()
            client = factory.for_node(node_id)
            probe = capture(client, point, self.clock)
            result = self._save(point, probe)
        except CollectionDatabaseError:
            result = ObservationResult(node_id, "error", error_code="database_unavailable")
        except AdapterError as error:
            result = ObservationResult(node_id, "error", error_code=error.code.value)
        except Exception:
            result = ObservationResult(node_id, "error", error_code="internal_error")
        finally:
            if factory is not None:
                try:
                    factory.close()
                except Exception:
                    result = replace(result, outcome="error", error_code="resource_close_failed")
        return result

    def _save(self, point: ProbePoint, probe: Probe) -> ObservationResult:
        count, failures = 0, 0
        with self._session() as session, session.begin():
            node = session.scalar(
                select(ManagedNode).where(ManagedNode.id == point.node_id).with_for_update()
            )
            if (
                node is None
                or node.connection_ref != point.reference
                or node.syncthing_device_id != point.expected_id
            ):
                return ObservationResult(point.node_id, "conflict")
            try:
                if (
                    node_connection(self.settings, point.reference, point.node_id)
                    != point.connection
                ):
                    return ObservationResult(point.node_id, "conflict")
            except AdapterError:
                return ObservationResult(point.node_id, "conflict")
            cache = node.enabled and (
                node.last_checked_at is None or probe.node_at >= node.last_checked_at
            )
            session.add(
                StatusSnapshot(
                    node_id=node.id,
                    scope="node",
                    observed_at=probe.node_at,
                    success=probe.node_error is None,
                    error_code=probe.node_error,
                )
            )
            count += 1
            if cache:
                code = probe.node_error or probe.config_error
                node.api_status = {
                    None: "ok",
                    "timeout": "unreachable",
                    "unreachable": "unreachable",
                    "auth_error": "auth_error",
                    "invalid_response": "invalid_response",
                    "identity_mismatch": "identity_mismatch",
                    "session_changed": "unknown",
                    "not_found": "unknown",
                    "internal_error": "unknown",
                }[code]
                node.last_checked_at = max(
                    probe.finished_at, probe.node_at, node.last_seen_at or probe.node_at
                )
                if probe.system is not None:
                    node.last_seen_at = max(node.last_seen_at or probe.node_at, probe.node_at)
            if probe.configs is not None:
                folders = session.scalars(select(Folder).where(Folder.node_id == node.id)).all()
                indexed = {row.syncthing_folder_id: row for row in folders}
                if cache:
                    present = {row.syncthing_folder_id for row in probe.configs}
                    for folder in folders:
                        if (
                            folder.config_observed_at is None
                            or folder.config_observed_at <= probe.node_at
                        ):
                            folder.present = folder.syncthing_folder_id in present
                for config in probe.configs:
                    folder = indexed.get(config.syncthing_folder_id)
                    if folder is None:
                        folder = Folder(
                            node_id=node.id,
                            syncthing_folder_id=config.syncthing_folder_id,
                            label=config.label,
                            present=False,
                        )
                        session.add(folder)
                        session.flush()
                    session.add(
                        StatusSnapshot(
                            node_id=node.id,
                            folder_id=folder.id,
                            scope="folder",
                            observed_at=config.observed_at,
                            success=True,
                            actual_paused=config.paused,
                        )
                    )
                    count += 1
                    if cache and (
                        folder.config_observed_at is None
                        or config.observed_at >= folder.config_observed_at
                    ):
                        folder.label, folder.present = config.label, True
                        folder.actual_paused, folder.config_observed_at = (
                            config.paused,
                            config.observed_at,
                        )
                    runtime = probe.runtime[config.syncthing_folder_id]
                    if isinstance(runtime, tuple):
                        at, error = runtime
                        session.add(
                            StatusSnapshot(
                                node_id=node.id,
                                folder_id=folder.id,
                                scope="folder",
                                observed_at=at,
                                success=False,
                                error_code="runtime_" + error,
                            )
                        )
                        failures += 1
                    else:
                        session.add(
                            StatusSnapshot(
                                node_id=node.id,
                                folder_id=folder.id,
                                scope="folder",
                                observed_at=runtime.observed_at,
                                success=True,
                                runtime_state=runtime.state,
                                need_bytes=runtime.need_bytes,
                                pull_errors=runtime.pull_errors,
                            )
                        )
                        if cache and (
                            folder.runtime_observed_at is None
                            or runtime.observed_at >= folder.runtime_observed_at
                        ):
                            folder.runtime_state, folder.runtime_observed_at = (
                                runtime.state,
                                runtime.observed_at,
                            )
                            folder.need_bytes, folder.pull_errors = (
                                runtime.need_bytes,
                                runtime.pull_errors,
                            )
                    count += 1
        return ObservationResult(
            point.node_id, "stored", count, cache, probe.node_error, probe.config_error, failures
        )
