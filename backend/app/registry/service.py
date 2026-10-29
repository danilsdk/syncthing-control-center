"""Atomic registry writes; all source I/O happens outside database transactions."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.contracts import dto
from app.contracts.errors import AppError
from app.contracts.interfaces import (
    AdapterError,
    AdapterFactory,
    Clock,
    FolderConfig,
    FolderRuntime,
    NodeConnection,
    SyncthingAdapter,
    SystemObservation,
)
from app.db import Database, DatabaseNotReady
from app.models import Folder, ManagedNode, SyncRule
from app.registry.projections import DesiredState, folder_dto, node_dto
from app.registry.resolver import node_connection, resolve_record
from app.syncthing_adapter import HttpSyncthingAdapter

ProbeFactory = Callable[[NodeConnection, str | None], SyncthingAdapter]


@dataclass(frozen=True)
class Observation:
    system: SystemObservation
    folders: tuple[FolderConfig, ...]
    runtime: dict[str, FolderRuntime]


def capture(client: SyncthingAdapter) -> Observation:
    before = client.read_system()
    folders = tuple(client.list_folders())
    if len({folder.syncthing_folder_id for folder in folders}) != len(folders):
        raise AdapterError("invalid_response")
    runtime = {}
    for folder in folders:
        try:
            runtime[folder.syncthing_folder_id] = client.read_runtime(folder.syncthing_folder_id)
        except AdapterError as error:
            if error.code in {"identity_mismatch", "session_changed"}:
                raise
            # A runtime failure does not fabricate success or erase prior runtime.
    after = client.read_system()
    if after.device_id != before.device_id:
        raise AdapterError("identity_mismatch")
    if after.source_session_id != before.source_session_id:
        raise AdapterError("session_changed")
    return Observation(after, folders, runtime)


class RegistryService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Clock,
        adapters: AdapterFactory | None = None,
        *,
        probe_factory: ProbeFactory | None = None,
        desired_state: DesiredState | None = None,
    ) -> None:
        self.settings, self.database, self.clock = settings, database, clock
        self.adapters, self.desired_state = adapters, desired_state
        self.probe_factory = probe_factory if probe_factory is not None else self._probe

    def _probe(self, connection: NodeConnection, expected: str | None) -> SyncthingAdapter:
        return HttpSyncthingAdapter(connection, expected_device_id=expected, clock=self.clock)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with self.database.session() as session:
                yield session
        except (DatabaseNotReady, SQLAlchemyError):
            raise AppError(503, "service_unavailable", "Database is unavailable") from None

    @staticmethod
    def _node(session: Session, identifier: UUID, *, locked: bool = False) -> ManagedNode:
        statement = select(ManagedNode).where(ManagedNode.id == identifier)
        if locked:
            statement = statement.with_for_update()
        node = session.scalar(statement)
        if node is None:
            raise AppError(404, "node_not_found", "Node was not found")
        return node

    def connections(self, limit: int, offset: int) -> dto.ConnectionPage:
        items = sorted(
            self.settings.connections.items(), key=lambda entry: (entry[1].display_name, entry[0])
        )
        return dto.ConnectionPage(
            items=[
                dto.Connection(connection_ref=ref, display_name=value.display_name)
                for ref, value in items[offset : offset + limit]
            ],
            total=len(items),
            limit=limit,
            offset=offset,
        )

    def nodes(self, limit: int, offset: int) -> dto.NodePage:
        with self._session() as session:
            count = session.scalar(select(func.count()).select_from(ManagedNode))
            rows = session.scalars(
                select(ManagedNode)
                .order_by(ManagedNode.display_name, ManagedNode.id)
                .limit(limit)
                .offset(offset)
            ).all()
            now = self.clock.now_utc()
            return dto.NodePage(
                items=[node_dto(n, now, self.settings.stale_after_seconds) for n in rows],
                total=count,
                limit=limit,
                offset=offset,
            )

    @staticmethod
    def _registration_error(error: AdapterError) -> AppError:
        mapping = {
            "timeout": (504, "upstream_timeout"),
            "unreachable": (502, "upstream_unreachable"),
            "auth_error": (502, "upstream_auth_error"),
            "invalid_response": (502, "upstream_invalid_response"),
            "identity_mismatch": (502, "upstream_invalid_response"),
            "not_found": (502, "upstream_invalid_response"),
            "session_changed": (503, "service_unavailable"),
        }
        status, code = mapping[error.code]
        return AppError(status, code, "Source verification failed")

    @staticmethod
    def _duplicate() -> AppError:
        return AppError(409, "connection_already_registered", "Connection is already registered")

    def register(self, payload: dto.NodeCreate) -> dto.Node:
        if payload.connection_ref not in self.settings.connections:
            raise AppError(422, "connection_not_allowed", "Connection is not allowed")
        with self._session() as session:
            if (
                session.scalar(
                    select(ManagedNode.id).where(
                        ManagedNode.connection_ref == payload.connection_ref
                    )
                )
                is not None
            ):
                raise self._duplicate()
        identifier = uuid4()
        try:
            connection = node_connection(self.settings, payload.connection_ref, identifier)
        except AdapterError:
            raise AppError(
                503, "service_unavailable", "Connection changed during verification"
            ) from None
        try:
            discovery = self.probe_factory(connection, None)
            try:
                discovered = discovery.read_system()
            finally:
                discovery.close()
            client = self.probe_factory(connection, discovered.device_id)
            try:
                observed = capture(client)
            finally:
                client.close()
            if observed.system.device_id != discovered.device_id:
                raise AdapterError("identity_mismatch")
        except AdapterError as error:
            raise self._registration_error(error) from None
        # Server configuration may have changed during network reads.
        try:
            unchanged = (
                node_connection(self.settings, payload.connection_ref, identifier) == connection
            )
        except AdapterError:
            unchanged = False
        if not unchanged:
            raise AppError(503, "service_unavailable", "Connection changed during verification")
        with self._session() as session:
            node = ManagedNode(
                id=identifier,
                display_name=payload.display_name,
                connection_ref=payload.connection_ref,
                enabled=True,
                syncthing_device_id=observed.system.device_id,
                api_status="ok",
                last_checked_at=max(self.clock.now_utc(), observed.system.observed_at),
                last_seen_at=observed.system.observed_at,
                source_start_time_raw=observed.system.start_time_raw,
                source_session_id=observed.system.source_session_id,
                source_stream_anchor=observed.system.stream_anchor.model_dump(mode="json"),
                last_event_id=0,
            )
            session.add(node)
            try:
                session.flush()
                self._folders(session, node.id, observed)
                session.commit()
            except IntegrityError as error:
                session.rollback()
                if (
                    getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                    == "uq_managed_nodes_connection_ref"
                ):
                    raise self._duplicate() from None
                raise
            session.refresh(node)
            return node_dto(node, self.clock.now_utc(), self.settings.stale_after_seconds)

    @staticmethod
    def _folders(session: Session, node_id: UUID, observed: Observation) -> None:
        rows = session.scalars(select(Folder).where(Folder.node_id == node_id)).all()
        indexed = {row.syncthing_folder_id: row for row in rows}
        present = {item.syncthing_folder_id for item in observed.folders}
        for row in rows:
            row.present = row.syncthing_folder_id in present
        for config in observed.folders:
            row = indexed.get(config.syncthing_folder_id)
            if row is None:
                row = Folder(node_id=node_id, syncthing_folder_id=config.syncthing_folder_id)
                session.add(row)
            row.label, row.present = config.label, True
            row.actual_paused, row.config_observed_at = config.paused, config.observed_at
            runtime = observed.runtime.get(config.syncthing_folder_id)
            if runtime is not None:
                row.runtime_state, row.runtime_observed_at = runtime.state, runtime.observed_at
                row.need_bytes, row.pull_errors = runtime.need_bytes, runtime.pull_errors

    def refresh_node(self, identifier: UUID) -> None:
        failure, target, observed = None, None, None
        with self._session() as session:
            node = self._node(session, identifier)
            now = self.clock.now_utc()
            if not node.enabled:
                return
            if (
                node.last_checked_at is not None
                and 0
                <= (now - node.last_checked_at).total_seconds()
                < self.settings.observation_interval_seconds
            ):
                return
            reference, expected, revision = (
                node.connection_ref,
                node.syncthing_device_id,
                node.updated_at,
            )
            try:
                target = resolve_record(self.settings, node)
            except AdapterError as error:
                failure = error
        # No SQL session or row lock spans adapter resolution/network operations.
        try:
            if failure is not None:
                raise failure
            if self.adapters is None:
                raise AppError(503, "service_unavailable", "Syncthing adapter is unavailable")
            observed = capture(self.adapters.for_node(identifier))
            if observed.system.device_id != expected:
                raise AdapterError("identity_mismatch")
        except AdapterError as error:
            failure = error
        with self._session() as session:
            node = self._node(session, identifier, locked=True)
            if (
                not node.enabled
                or node.updated_at != revision
                or node.connection_ref != reference
                or node.syncthing_device_id != expected
            ):
                return  # Concurrent patch/observation/cursor write invalidated our snapshot.
            if target is not None:
                try:
                    current = resolve_record(self.settings, node)
                except AdapterError:
                    return
                if current != target:
                    return
            checked = max(
                self.clock.now_utc(), node.last_checked_at or now, node.last_seen_at or now
            )
            if failure is not None:
                node.api_status = {
                    "timeout": "unreachable",
                    "unreachable": "unreachable",
                    "auth_error": "auth_error",
                    "invalid_response": "invalid_response",
                    "identity_mismatch": "identity_mismatch",
                    "not_found": "unknown",
                    "session_changed": "unknown",
                }[failure.code]
                if reference not in self.settings.connections:
                    node.api_status = "unknown"
                node.last_checked_at = checked
            else:
                node.api_status = "ok"
                node.last_seen_at = max(
                    node.last_seen_at or observed.system.observed_at, observed.system.observed_at
                )
                node.last_checked_at = max(checked, node.last_seen_at)
                self._folders(session, node.id, observed)
            # Canonical source checkpoint/cursor is deliberately not assigned here.
            session.commit()

    def node(self, identifier: UUID) -> dto.Node:
        self.refresh_node(identifier)
        with self._session() as session:
            return node_dto(
                self._node(session, identifier),
                self.clock.now_utc(),
                self.settings.stale_after_seconds,
            )

    def patch(self, identifier: UUID, payload: dto.NodePatch) -> dto.Node:
        with self._session() as session:
            node = self._node(session, identifier, locked=True)
            for key, value in payload.changes().items():
                setattr(node, key, value)
            session.commit()
            session.refresh(node)
            return node_dto(node, self.clock.now_utc(), self.settings.stale_after_seconds)

    def folders(self, identifier: UUID, limit: int, offset: int) -> dto.FolderPage:
        self.refresh_node(identifier)
        with self._session() as session:
            node = self._node(session, identifier)
            count = session.scalar(
                select(func.count()).select_from(Folder).where(Folder.node_id == identifier)
            )
            rows = session.scalars(
                select(Folder)
                .where(Folder.node_id == identifier)
                .order_by(Folder.created_at.desc(), Folder.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            rules = session.scalars(
                select(SyncRule).where(
                    SyncRule.folder_id.in_([row.id for row in rows]),
                    SyncRule.enabled.is_(True),
                )
            ).all()
            indexed = {rule.folder_id: rule for rule in rules}
            now = self.clock.now_utc()
            return dto.FolderPage(
                items=[
                    folder_dto(
                        row,
                        node,
                        indexed.get(row.id),
                        self.desired_state,
                        now,
                        self.settings.stale_after_seconds,
                    )
                    for row in rows
                ],
                total=count,
                limit=limit,
                offset=offset,
            )
