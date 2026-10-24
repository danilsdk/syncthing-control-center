from types import SimpleNamespace
from unittest.mock import MagicMock

import app.db as db_module
import pytest
from app.config import Settings
from app.db import Database, DatabaseNotReady
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError


def fake_engine_database():
    database = Database(Settings())
    database.engine = MagicMock(spec=Engine)
    connection = database.engine.connect.return_value.__enter__.return_value
    return database, connection


def test_engine_creation_is_lazy_and_postgresql_only(monkeypatch):
    connect = MagicMock(side_effect=AssertionError("Unit tests must not connect"))
    monkeypatch.setattr(Engine, "connect", connect)
    database = Database(Settings(database_url="postgresql+psycopg://demo:test-only@localhost/demo"))
    assert database.engine.dialect.name == "postgresql"
    assert database.engine.hide_parameters is True
    connect.assert_not_called()
    database.close()


def test_unconfigured_database_cannot_be_ready_or_provide_session():
    database = Database(Settings())
    with pytest.raises(DatabaseNotReady, match="database_not_configured"):
        database.check_ready()
    with pytest.raises(DatabaseNotReady, match="database_not_configured"), database.session():
        pytest.fail("An unconfigured database must not provide a session")


def test_connection_failure_is_redacted():
    database, _ = fake_engine_database()
    database.engine.connect.side_effect = OperationalError(
        "connect", None, Exception("db-password-canary")
    )
    with pytest.raises(DatabaseNotReady) as error:
        database.check_ready()
    assert str(error.value) == "database_unavailable"
    assert "db-password-canary" not in str(error.value)


def test_empty_migrations_do_not_produce_false_readiness(monkeypatch, tmp_path):
    database, _ = fake_engine_database()
    monkeypatch.setattr(db_module, "MIGRATIONS_DIRECTORY", tmp_path)
    with pytest.raises(DatabaseNotReady, match="schema_not_ready"):
        database.check_ready()


@pytest.mark.parametrize(
    "expected,actual,ready",
    [
        (["revision-1"], ["revision-1"], True),
        (["revision-1"], [], False),
        (["revision-2"], ["revision-1"], False),
        (["branch-a", "branch-b"], ["branch-a", "branch-b"], False),
    ],
)
def test_readiness_requires_one_matching_migration_head(monkeypatch, expected, actual, ready):
    database, connection = fake_engine_database()
    monkeypatch.setattr(
        db_module, "ScriptDirectory", lambda _: SimpleNamespace(get_heads=lambda: expected)
    )
    monkeypatch.setattr(
        db_module.MigrationContext,
        "configure",
        lambda _: SimpleNamespace(get_current_heads=lambda: actual),
    )
    if ready:
        database.check_ready()
    else:
        with pytest.raises(DatabaseNotReady, match="schema_not_ready"):
            database.check_ready()
    assert all(
        str(call.args[0]).upper().startswith("SELECT") for call in connection.execute.call_args_list
    )


def test_close_disposes_connection_pool():
    database, _ = fake_engine_database()
    database.close()
    database.engine.dispose.assert_called_once_with()
