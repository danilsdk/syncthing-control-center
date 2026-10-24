"""Shared dependencies for future domain routers."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.contracts.errors import AppError
from app.db import Database, DatabaseNotReady


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_session(request: Request) -> Iterator[Session]:
    database = get_database(request)
    try:
        with database.session() as session:
            yield session
    except DatabaseNotReady:
        raise AppError(503, "service_unavailable", "Database is not configured") from None
