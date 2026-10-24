"""Shared dependencies for future domain routers."""

from collections.abc import Iterator
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.contracts.errors import AppError
from app.contracts.interfaces import AdapterFactory, Clock, SyncthingAdapter
from app.contracts.locks import AdvisoryLocks
from app.db import Database, DatabaseNotReady


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def get_adapter_factory(request: Request) -> AdapterFactory:
    factory = request.app.state.adapters
    if factory is None:
        raise AppError(503, "service_unavailable", "Syncthing adapter is not configured")
    return factory


def get_adapter(node_id: UUID, request: Request) -> SyncthingAdapter:
    return get_adapter_factory(request).for_node(node_id)


def get_folder_locks(request: Request) -> AdvisoryLocks:
    database = get_database(request)
    if database.engine is None:
        raise AppError(503, "service_unavailable", "Database is not configured")
    return AdvisoryLocks(database.engine)


def get_session(request: Request) -> Iterator[Session]:
    database = get_database(request)
    try:
        with database.session() as session:
            yield session
    except DatabaseNotReady:
        raise AppError(503, "service_unavailable", "Database is not configured") from None
