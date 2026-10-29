"""Synchronous boundaries for A1/A2/A3 and B1/B2; no HTTP implementation here."""

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AfterValidator, Field, SecretStr, StrictBool, field_validator, model_validator

from app.contracts.dto import EventPayload
from app.contracts.types import (
    DTO,
    DisplayName,
    NonEmpty,
    NonNegative,
    Positive,
    SessionId,
    SyncthingFolderId,
    UtcDateTime,
)


@runtime_checkable
class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


def _raw_timestamp(value: str) -> str:
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("Expected an RFC 3339 source timestamp")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Expected a valid source timestamp") from None
    return value  # Never round-trip through datetime: keep the exact nanoseconds/string.


RawTimestamp = Annotated[NonEmpty, AfterValidator(_raw_timestamp)]


def source_session_id(node_id: UUID, start_time_raw: str) -> str:
    if not isinstance(node_id, UUID):
        raise TypeError("Expected an internal node UUID")
    raw = _raw_timestamp(start_time_raw)
    return hashlib.sha256(f"{node_id}\n{raw}".encode()).hexdigest()


class NodeConnection(DTO):
    node_id: UUID
    base_url: SecretStr = Field(exclude=True, repr=False)
    api_key: SecretStr = Field(exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def require_root_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        try:
            url = urlsplit(raw)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and url.username is None
                and url.password is None
                and url.path in {"", "/"}
                and not url.query
                and not url.fragment
                and "*" not in raw
                and not any(c.isspace() for c in raw)
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Expected a server HTTP(S) origin")
        return SecretStr(raw.rstrip("/"))

    @field_validator("api_key")
    @classmethod
    def require_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key must not be blank")
        return value


class SystemObservation(DTO):
    device_id: NonEmpty
    start_time_raw: RawTimestamp
    source_session_id: SessionId
    observed_at: UtcDateTime

    @classmethod
    def from_source(
        cls, *, node_id: UUID, device_id: str, start_time_raw: str, observed_at: datetime
    ) -> Self:
        return cls(
            device_id=device_id,
            start_time_raw=start_time_raw,
            source_session_id=source_session_id(node_id, start_time_raw),
            observed_at=observed_at,
        )


class FolderConfig(DTO):
    syncthing_folder_id: SyncthingFolderId
    label: DisplayName
    paused: StrictBool
    observed_at: UtcDateTime


class FolderRuntime(DTO):
    state: NonEmpty
    need_bytes: NonNegative | None
    pull_errors: NonNegative | None
    observed_at: UtcDateTime


class SourceEvent(DTO):
    id: Positive
    global_id: NonNegative | None
    type: NonEmpty
    time_raw: RawTimestamp
    syncthing_folder_id: SyncthingFolderId | None
    payload_summary: EventPayload


class EventBatch(DTO):
    source_session_id: SessionId
    events: tuple[SourceEvent, ...]
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def check_order(self) -> Self:
        ids = tuple(event.id for event in self.events)
        if any(left >= right for left, right in zip(ids, ids[1:], strict=False)):
            raise ValueError("Source positions must be strictly increasing")
        return self


class AdapterErrorCode(StrEnum):
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    AUTH_ERROR = "auth_error"
    NOT_FOUND = "not_found"
    INVALID_RESPONSE = "invalid_response"
    IDENTITY_MISMATCH = "identity_mismatch"
    SESSION_CHANGED = "session_changed"


class AdapterError(Exception):
    """Only fixed messages, never upstream exception text, URLs or response bodies."""

    def __init__(
        self,
        code: AdapterErrorCode | str,
        *,
        operation: Literal["read", "write"] = "read",
        write_outcome: Literal["not_applied", "unknown"] | None = None,
    ) -> None:
        try:
            self.code = AdapterErrorCode(code)
        except ValueError:
            raise ValueError("Unknown adapter error code") from None
        if operation not in {"read", "write"} or (
            operation == "read" and write_outcome is not None
        ):
            raise ValueError("Invalid adapter error operation")
        if write_outcome not in {None, "not_applied", "unknown"}:
            raise ValueError("Invalid write outcome")
        self.retryable = self.code in {
            AdapterErrorCode.TIMEOUT,
            AdapterErrorCode.UNREACHABLE,
            AdapterErrorCode.SESSION_CHANGED,
        }
        self.write_outcome = (write_outcome or "unknown") if operation == "write" else None
        super().__init__(f"Syncthing operation failed: {self.code.value}")


@runtime_checkable
class SyncthingAdapter(Protocol):
    def read_system(self) -> SystemObservation: ...
    def list_folders(self) -> list[FolderConfig]: ...
    def read_folder(self, folder_id: str) -> FolderConfig: ...
    def read_runtime(self, folder_id: str) -> FolderRuntime: ...
    def set_paused(self, folder_id: str, paused: bool) -> None: ...
    def restart_required(self) -> bool: ...
    def read_events(self, since: int, timeout_seconds: int = 15) -> EventBatch: ...


@runtime_checkable
class AdapterFactory(Protocol):
    def for_node(self, node_id: UUID) -> SyncthingAdapter: ...
    def close(self) -> None:
        """Close clients owned by this factory; called by the application/worker owner."""
        ...
