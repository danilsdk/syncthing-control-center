import hmac
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.auth import require_admin
from app.api.dependencies import get_adapter
from app.config import Settings
from app.contracts.dto import User
from app.contracts.interfaces import AdapterFactory, SyncthingAdapter
from app.db import Database
from app.main import create_app
from app.models import Base
from app.registry.admin import AdminRecord
from app.registry.authentication import AdministratorAuth
from app.registry.security import LoginLimiter, PasswordHasher
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

PASSWORD = "public-auth-test-password-canary"
ORIGIN = "http://localhost:8000"
SECRET = "public-test-session-secret-canary" * 2


class FastTestHasher(PasswordHasher):
    """Component tests isolate HTTP/DB behavior; real scrypt has separate tests."""

    def __init__(self):
        self.targets = []

    def validate(self, encoded):
        if encoded != "test-only-hash":
            raise ValueError("Invalid test hash")

    def verify(self, password, encoded):
        self.targets.append(encoded)
        return encoded == "test-only-hash" and hmac.compare_digest(password, PASSWORD)


class MemoryStore:
    def __init__(self):
        self.current = AdminRecord(uuid4(), "admin", "test-only-hash")
        self.logins = []
        self.failure = None
        self.changed_during_login = False

    def administrator(self):
        if self.failure:
            raise self.failure
        return self.current

    def mark_login(self, admin, now):
        if self.changed_during_login:
            self.current = replace(admin, is_active=False)
            return False
        self.logins.append(now)
        return True


class FakeClock:
    def __init__(self):
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.tick = 0.0

    def now_utc(self):
        return self.now


@pytest.fixture
def context():
    settings = Settings(session_secret=SECRET)
    store, clock, hasher = MemoryStore(), FakeClock(), FastTestHasher()
    auth = AdministratorAuth(
        settings, store, clock, hasher=hasher, limiter=LoginLimiter(monotonic=lambda: clock.tick)
    )
    adapters = MagicMock(spec=AdapterFactory)
    router = APIRouter()
    touched = []

    @router.api_route(
        "/test-resource",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def resource(user: Annotated[User, Depends(require_admin)]):
        touched.append(user.id)
        return {"ok": True}

    @router.get("/nodes/{node_id}/test-source")
    def source(adapter: Annotated[SyncthingAdapter, Depends(get_adapter)]):
        return {"restart": adapter.restart_required()}

    @router.get("/test-secret-failure")
    def failure(request: Request):
        raise RuntimeError(PASSWORD + request.headers.get("cookie", ""))

    app = create_app(settings, routers=[router], auth=auth, clock=clock, adapters=adapters)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            app=app,
            client=client,
            settings=settings,
            store=store,
            clock=clock,
            auth=auth,
            hasher=hasher,
            adapters=adapters,
            touched=touched,
            password=PASSWORD,
            origin=ORIGIN,
            secret=SECRET,
        )


@pytest.fixture
def auth_database():
    value = os.environ.get("SCC_AUTH_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Real PostgreSQL: run tests/integration/auth/check_postgres.py explicitly")
    url = make_url(value)
    if (
        os.environ.get("SCC_AUTH_TEST_PRIVATE_CLUSTER") != "a22-private-cluster"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.username != "scc_test"
        or url.database != "scc_a22_tests"
        or not url.port
        or url.port == 5432
    ):
        pytest.fail("Refusing auth tests outside the explicitly owned private PostgreSQL")
    database = Database(Settings(database_url=value))
    try:
        root = Path(__file__).resolve().parents[3]
        config = Config(str(root / "backend/alembic.ini"))
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
            connection.execute(text(f"TRUNCATE TABLE {tables}"))
        yield database
    finally:
        database.close()
