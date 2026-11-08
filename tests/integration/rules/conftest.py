"""Opt-in fixtures for the real private PostgreSQL rules tests."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.auth import require_admin
from app.api.rules import router
from app.config import Settings
from app.contracts.dto import User
from app.db import Database
from app.main import create_app
from app.models import Base, Folder, ManagedNode
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def postgres_engine() -> Engine:
    value = os.environ.get("SCC_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Real PostgreSQL: run the private cluster runner explicitly")
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
        pytest.fail(
            "Refusing PostgreSQL tests outside the explicitly owned private cluster"
        )
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
    config = Config(str(ROOT / "backend/alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def graph(postgres_engine: Engine):
    node_id = uuid4()
    folder_id = uuid4()
    with postgres_engine.begin() as connection:
        tables = ", ".join(
            table.name for table in reversed(Base.metadata.sorted_tables)
        )
        connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
        connection.execute(
            insert(ManagedNode).values(
                id=node_id, display_name="Demo node", connection_ref="demo-node"
            )
        )
        connection.execute(
            insert(Folder).values(
                id=folder_id,
                node_id=node_id,
                syncthing_folder_id="shared",
                label="Shared",
            )
        )
    return node_id, folder_id


@pytest.fixture
def client(postgres_engine: Engine, graph):
    database = Database(Settings())
    database.engine = postgres_engine
    database._sessions = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    app = create_app(database=database, routers=[router])
    app.dependency_overrides[require_admin] = lambda: User(id=uuid4(), login="admin")
    with TestClient(app) as test_client:
        yield test_client
