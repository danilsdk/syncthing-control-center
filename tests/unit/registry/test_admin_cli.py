import importlib
from types import SimpleNamespace

import pytest
from app.db import DatabaseNotReady
from app.registry.admin import BootstrapError

cli = importlib.import_module("app.registry.__main__")


@pytest.mark.parametrize("outcome", ["success", "duplicate", "mismatch", "database"])
def test_local_admin_command_never_echoes_password_or_accepts_it_as_argument(
    monkeypatch, capsys, outcome
):
    password = "public-cli-password-canary"
    calls = []
    closed = []

    def ready():
        if outcome == "database":
            raise DatabaseNotReady("database_unavailable")

    database = SimpleNamespace(check_ready=ready, close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "Database", lambda _: database)
    values = iter([password, "different" if outcome == "mismatch" else password])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(values))

    def create(db, login, supplied):
        calls.append((db, login, supplied))
        if outcome == "duplicate":
            raise BootstrapError("do not echo " + password)

    monkeypatch.setattr(cli, "create_administrator", create)
    code = cli.main(["admin-create", "--login", "admin"])
    assert code == (0 if outcome == "success" else 1 if outcome == "database" else 2)
    assert password not in capsys.readouterr().out
    assert bool(calls) == (outcome in {"success", "duplicate"})
    assert closed == [True]


def test_password_argument_is_not_supported(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["admin-create", "--login", "admin", "--password", "not-a-real-password"])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "not-a-real-password" not in captured.out + captured.err


def test_cli_cleanup_failure_does_not_echo_exception(monkeypatch, capsys):
    def fail_close():
        raise RuntimeError("private-dsn-password-canary")

    database = SimpleNamespace(check_ready=lambda: None, close=fail_close)
    monkeypatch.setattr(cli, "Database", lambda _: database)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "public-test-password-canary")
    monkeypatch.setattr(cli, "create_administrator", lambda *_: None)
    assert cli.main(["admin-create", "--login", "admin"]) == 1
    assert "private-dsn-password-canary" not in capsys.readouterr().out
