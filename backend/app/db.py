"""Synchronous PostgreSQL access. No schema creation or startup connection."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[1] / "migrations"


class DatabaseNotReady(Exception):
    def __init__(
        self,
        reason: Literal["database_not_configured", "database_unavailable", "schema_not_ready"],
    ) -> None:
        super().__init__(reason)
        self.reason = reason


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine: Engine | None = None
        self._sessions: sessionmaker[Session] | None = None
        if settings.database_url is not None:
            self.engine = create_engine(
                settings.database_url.get_secret_value(),
                echo=False,
                hide_parameters=True,
                pool_pre_ping=True,
                pool_size=settings.db_pool_size,
                max_overflow=0,
                pool_timeout=settings.db_pool_timeout_seconds,
                connect_args={
                    "connect_timeout": settings.db_connect_timeout_seconds,
                    "options": (
                        f"-c statement_timeout={settings.db_statement_timeout_ms} "
                        f"-c lock_timeout={settings.db_statement_timeout_ms}"
                    ),
                },
            )
            self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        if self._sessions is None:
            raise DatabaseNotReady("database_not_configured")
        # Closing a session rolls back any uncommitted work; callers commit explicitly.
        with self._sessions() as session:
            yield session

    def check_ready(self) -> None:
        if self.engine is None:
            raise DatabaseNotReady("database_not_configured")
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                try:
                    expected = set(ScriptDirectory(str(MIGRATIONS_DIRECTORY)).get_heads())
                except Exception:
                    raise DatabaseNotReady("schema_not_ready") from None
                # A reachable database alone must not mark a schema-less app ready.
                if len(expected) != 1:
                    raise DatabaseNotReady("schema_not_ready")
                actual = set(MigrationContext.configure(connection).get_current_heads())
                if actual != expected:
                    raise DatabaseNotReady("schema_not_ready")
        except SQLAlchemyError:
            raise DatabaseNotReady("database_unavailable") from None

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
