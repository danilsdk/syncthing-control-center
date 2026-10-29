"""Explicit opt-in to the freshly initialized cluster owned by check_postgres.py."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.models import Base
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]


def migrate(connection, action="upgrade", revision="head"):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.attributes["connection"] = connection
    getattr(command, action)(config, revision)


@pytest.fixture(scope="module")
def postgres_engine():
    value = os.environ.get("SCC_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Real PostgreSQL: run docs/design/a/check_postgres.py explicitly")
    url = make_url(value)
    if (
        os.environ.get("SCC_TEST_PRIVATE_CLUSTER") != "a03-private-cluster"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.database != "scc_a03_tests"
        or url.username != "scc_test"
        or not url.port
        or url.port == 5432
    ):
        pytest.fail("Refusing PostgreSQL tests outside the explicitly owned private cluster")
    engine = create_engine(
        url,
        hide_parameters=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 3,
            "options": "-c statement_timeout=8000 -c lock_timeout=5000",
        },
    )
    try:
        with engine.begin() as connection:
            migrate(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg(postgres_engine):
    with postgres_engine.begin() as connection:
        names = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
        connection.execute(text(f"TRUNCATE TABLE {names}"))
    return postgres_engine
