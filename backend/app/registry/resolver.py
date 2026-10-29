"""Resolve internal UUIDs using server configuration and persisted identity/proof."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.contracts.interfaces import AdapterError, NodeConnection, SourceCheckpoint
from app.db import Database, DatabaseNotReady
from app.models import ManagedNode
from app.syncthing_adapter import ResolvedNode


def node_connection(settings: Settings, reference: str, node_id: UUID) -> NodeConnection:
    configured = settings.connections.get(reference)
    if configured is None:
        raise AdapterError("unreachable")
    return NodeConnection(node_id=node_id, base_url=configured.base_url, api_key=configured.api_key)


def resolve_record(settings: Settings, node: ManagedNode) -> ResolvedNode:
    if not node.enabled:
        raise AdapterError("unreachable")
    if not node.syncthing_device_id:
        raise AdapterError("identity_mismatch")
    checkpoint = None
    try:
        if node.source_session_id is not None:
            checkpoint = SourceCheckpoint(
                node_id=node.id,
                start_time_raw=node.source_start_time_raw,
                source_session_id=node.source_session_id,
                anchor=node.source_stream_anchor,
            )
        return ResolvedNode(
            node_connection(settings, node.connection_ref, node.id),
            expected_device_id=node.syncthing_device_id,
            checkpoint=checkpoint,
        )
    except (ValidationError, ValueError):
        raise AdapterError("invalid_response") from None


class NodeResolver:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings, self.database = settings, database

    def __call__(self, node_id: UUID) -> ResolvedNode:
        try:
            with self.database.session() as session:
                node = session.get(ManagedNode, node_id)
                if node is None:
                    raise AdapterError("not_found")
                return resolve_record(self.settings, node)
        except (DatabaseNotReady, SQLAlchemyError):
            raise AdapterError("unreachable") from None
