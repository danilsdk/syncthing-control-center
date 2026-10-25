import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.config import Settings
from app.contracts.interfaces import SystemClock
from app.main import create_app
from app.models import AppUser
from app.registry.admin import AdminStore, BootstrapError, create_administrator
from app.registry.security import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

pytestmark = pytest.mark.postgres
PASSWORD = "public-postgres-auth-password-canary"
ORIGIN = "http://localhost:8000"


def test_real_bootstrap_login_activity_and_delete(auth_database):
    database = auth_database
    identifier = create_administrator(database, " Admin ", PASSWORD)
    settings = Settings(session_secret="public-postgres-session-test-secret" * 2)
    with TestClient(create_app(settings, database=database)) as client:
        response = client.post(
            "/api/auth/login",
            json={"login": "ADMIN", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        assert response.json()["user"] == {"id": str(identifier), "login": "admin"}
        assert client.get("/api/auth/me").status_code == 200
        with database.session() as session:
            row = session.get(AppUser, identifier)
            assert row.last_login_at is not None and row.password_hash != PASSWORD
            row.is_active = False
            session.commit()
        assert client.get("/api/auth/me").status_code == 401
        with database.session() as session:
            session.execute(delete(AppUser))
            session.commit()
        assert client.get("/api/auth/me").status_code == 503


def test_concurrent_bootstrap_different_logins_creates_only_one_admin(auth_database, monkeypatch):
    barrier = Barrier(2)
    original = PasswordHasher.hash

    def synchronized(self, password):
        result = original(self, password)
        barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(PasswordHasher, "hash", synchronized)

    def create(login):
        try:
            create_administrator(auth_database, login, PASSWORD)
            return "created"
        except BootstrapError:
            return "existing"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ["first", "second"]))
    assert sorted(results) == ["created", "existing"]
    with auth_database.session() as session:
        assert session.scalar(select(func.count()).select_from(AppUser)) == 1


@pytest.mark.parametrize("change", ["inactive", "password", "deleted"])
def test_store_rechecks_identity_before_marking_login(auth_database, change, monkeypatch):
    monkeypatch.setattr(PasswordHasher, "hash", lambda *_: "test-only-private-store-hash")
    identifier = create_administrator(auth_database, "admin", PASSWORD)
    store = AdminStore(auth_database)
    before = store.administrator()
    with auth_database.session() as session:
        if change == "deleted":
            session.execute(delete(AppUser))
        else:
            changes = (
                {"is_active": False} if change == "inactive" else {"password_hash": "changed-hash"}
            )
            session.execute(update(AppUser).where(AppUser.id == identifier).values(**changes))
        session.commit()
    assert not store.mark_login(before, SystemClock().now_utc())
    with auth_database.session() as session:
        row = session.get(AppUser, identifier)
        assert row is None or row.last_login_at is None


def test_two_existing_users_close_auth_instead_of_selecting_an_arbitrary_admin(auth_database):
    with auth_database.session() as session:
        session.add_all(
            [AppUser(login=name, password_hash="not-a-real-hash") for name in ["one", "two"]]
        )
        session.commit()
    assert AdminStore(auth_database).administrator() is None


def test_real_operator_command_does_not_echo_or_replace_admin(auth_database, monkeypatch, capsys):
    cli = importlib.import_module("app.registry.__main__")
    # Use the private URL through the same production Settings path.
    monkeypatch.setenv(
        "SCC_DATABASE_URL", auth_database.engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: PASSWORD)
    assert cli.main(["admin-create", "--login", "admin"]) == 0
    with auth_database.session() as session:
        before = session.scalar(select(AppUser.password_hash))
    assert cli.main(["admin-create", "--login", "second"]) == 2
    with auth_database.session() as session:
        assert session.scalar(select(AppUser.password_hash)) == before
        assert session.scalar(select(func.count()).select_from(AppUser)) == 1
    assert PASSWORD not in capsys.readouterr().out
