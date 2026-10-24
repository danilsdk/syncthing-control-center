"""Server-side resolution and ownership; registry/database integration belongs to A2."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from uuid import UUID

from app.contracts.interfaces import AdapterError, Clock, NodeConnection, SystemClock
from app.syncthing_adapter.client import HttpSyncthingAdapter
from app.syncthing_adapter.transport import HttpTransport


@dataclass(frozen=True)
class ResolvedNode:
    connection: NodeConnection
    expected_device_id: str | None = None  # None is permitted only for initial discovery.


class HttpAdapterFactory:
    def __init__(
        self,
        resolver: Callable[[UUID], ResolvedNode],
        *,
        clock: Clock | None = None,
        transport_factory=HttpTransport,
    ):
        self._resolver = resolver
        self._clock = clock or SystemClock()
        self._transport_factory = transport_factory
        self._cache = {}
        self._lock = RLock()
        self._closed = False

    def for_node(self, node_id: UUID) -> HttpSyncthingAdapter:
        if not isinstance(node_id, UUID):
            raise TypeError("Expected an internal node UUID")
        with self._lock:
            if self._closed:
                raise AdapterError("unreachable")
            try:
                target = self._resolver(node_id)
            except Exception as error:
                cached = self._cache.pop(node_id, None)
                if cached is not None:
                    cached[1].close()
                code = (
                    error.code
                    if isinstance(error, AdapterError)
                    else "not_found"
                    if isinstance(error, KeyError)
                    else "unreachable"
                )
                raise AdapterError(code) from None
            if (
                not isinstance(target, ResolvedNode)
                or not isinstance(target.connection, NodeConnection)
                or target.connection.node_id != node_id
            ):
                raise ValueError("Resolver must return the requested node connection")
            existing = self._cache.get(node_id)
            if existing is not None and existing[0] == target:
                return existing[1]
            if existing is not None:
                existing[1].close()
                del self._cache[node_id]
            transport = self._transport_factory(target.connection)
            try:
                adapter = HttpSyncthingAdapter(
                    target.connection,
                    expected_device_id=target.expected_device_id,
                    clock=self._clock,
                    transport=transport,
                )
            except Exception:
                transport.close()
                raise
            self._cache[node_id] = (target, adapter)
            return adapter

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for _, adapter in self._cache.values():
                adapter.close()
            self._cache.clear()
