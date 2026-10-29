"""Explicit environment configuration; local files are not read implicitly."""

import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.contracts.types import DisplayName


def _http_root(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "*" not in value
            and not any(character.isspace() for character in value)
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Expected an HTTP(S) origin without credentials, path or query")
    return value.rstrip("/")


class ConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    display_name: DisplayName
    base_url: SecretStr = Field(repr=False, exclude=True)
    api_key: SecretStr = Field(repr=False, exclude=True)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: SecretStr) -> SecretStr:
        return SecretStr(_http_root(value.get_secret_value()))

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key must not be empty")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCC_",
        env_file=None,
        extra="forbid",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr | None = Field(default=None, repr=False, exclude=True)
    session_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    allowed_origins: tuple[str, ...] = ("http://localhost:8000",)
    connections: dict[str, ConnectionSettings] = Field(default_factory=dict)
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=3000, ge=100, le=30000)
    db_pool_size: int = Field(default=5, ge=1, le=10)
    db_pool_timeout_seconds: int = Field(default=3, ge=1, le=30)
    reconciliation_interval_seconds: int = Field(default=15, ge=1, le=3600)
    observation_interval_seconds: int = Field(default=30, ge=1, le=3600)
    stale_after_seconds: int = Field(default=60, ge=1, le=86400)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not url.database:
                raise ValueError
        except (ArgumentError, ValueError):
            raise ValueError("Use a postgresql+psycopg URL with a database name") from None
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("At least one explicit origin is required")
        return tuple(dict.fromkeys(_http_root(origin) for origin in value))

    @field_validator("connections")
    @classmethod
    def validate_connection_refs(
        cls, value: dict[str, ConnectionSettings]
    ) -> dict[str, ConnectionSettings]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", ref) for ref in value):
            raise ValueError("Connection names must be server aliases, not URLs")
        return value

    @field_validator("session_secret")
    @classmethod
    def validate_session_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("Session secret must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_production_origins(self) -> Self:
        if self.environment == "production" and any(
            not origin.startswith("https://") for origin in self.allowed_origins
        ):
            raise ValueError("Production origins must use HTTPS")
        if self.stale_after_seconds < self.observation_interval_seconds:
            raise ValueError("Staleness threshold must not be shorter than observation interval")
        return self
