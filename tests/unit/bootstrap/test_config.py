import json

import pytest
from app.config import Settings
from app.contracts.dto import Connection
from pydantic import ValidationError


def test_environment_uses_explicit_prefix_and_does_not_read_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SCC_DATABASE_URL=not-a-real-database", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "unrelated-environment-value")
    assert Settings().database_url is None
    monkeypatch.setenv("SCC_DATABASE_URL", "postgresql+psycopg://demo:local-only@localhost/demo")
    assert Settings().database_url is not None


def test_settings_do_not_serialize_or_display_secrets(monkeypatch):
    markers = ["database-password-canary", "node-key-canary", "signing-secret-canary" * 2]
    monkeypatch.setenv("SCC_DATABASE_URL", f"postgresql+psycopg://demo:{markers[0]}@localhost/demo")
    monkeypatch.setenv("SCC_SESSION_SECRET", markers[2])
    monkeypatch.setenv(
        "SCC_CONNECTIONS",
        json.dumps(
            {
                "node-a": {
                    "display_name": "Demo A",
                    "base_url": "http://syncthing-a:8384",
                    "api_key": markers[1],
                }
            }
        ),
    )
    settings = Settings()
    displayed = repr(settings) + settings.model_dump_json()
    assert all(marker not in displayed for marker in markers)
    assert "syncthing-a" not in displayed
    assert settings.connections["node-a"].api_key.get_secret_value() == markers[1]


@pytest.mark.parametrize(
    "url", ["sqlite:///demo.db", "postgresql://localhost/demo", "invalid-canary"]
)
def test_rejects_other_database_drivers_without_echoing_input(url):
    with pytest.raises(ValidationError) as error:
        Settings(database_url=url)
    assert url not in str(error.value)


@pytest.mark.parametrize(
    "origin",
    [
        "https://*",
        "https://user:password@demo.test",
        "https://demo.test/api",
        "https://demo test",
        "\nhttps://demo.test",
    ],
)
def test_rejects_ambiguous_origins(origin):
    with pytest.raises(ValidationError):
        Settings(allowed_origins=(origin,))


def test_production_requires_https_origins():
    with pytest.raises(ValidationError):
        Settings(environment="production")
    assert (
        Settings(environment="production", allowed_origins=("https://demo.test",)).environment
        == "production"
    )


def test_connection_settings_are_validated_without_displaying_secret(monkeypatch):
    secret = "must-not-appear-in-validation"
    monkeypatch.setenv(
        "SCC_CONNECTIONS",
        json.dumps(
            {
                "node-a": {
                    "display_name": "Demo",
                    "base_url": f"http://demo.test/?key={secret}",
                    "api_key": secret,
                }
            }
        ),
    )
    with pytest.raises(ValidationError) as error:
        Settings()
    assert secret not in str(error.value)


def test_rejects_connection_url_as_alias():
    with pytest.raises(ValidationError):
        Settings(
            connections={
                "http://demo.test": {
                    "display_name": "Demo",
                    "base_url": "http://demo.test",
                    "api_key": "test-only",
                }
            }
        )


@pytest.mark.parametrize("name", ["   ", "\t\n"])
def test_connection_settings_reject_names_the_public_dto_cannot_display(name):
    with pytest.raises(ValidationError):
        Settings(
            connections={
                "node-a": {
                    "display_name": name,
                    "base_url": "http://127.0.0.1:18384",
                    "api_key": "name-validation-secret-canary",
                }
            }
        )


def test_configured_connection_name_round_trips_through_the_public_dto():
    name = "  Узел A  "
    settings = Settings(
        connections={
            "node-a": {
                "display_name": name,
                "base_url": "http://127.0.0.1:18384",
                "api_key": "name-validation-secret-canary",
            }
        }
    )
    connection = Connection(connection_ref="node-a", **settings.connections["node-a"].model_dump())
    assert connection.display_name == name


@pytest.mark.parametrize(
    "changes", [{"db_connect_timeout_seconds": 0}, {"db_pool_size": 0}, {"stale_after_seconds": 10}]
)
def test_rejects_unbounded_or_inconsistent_settings(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)
