"""Wire primitives shared by DTOs, adapters and module consumers."""

import re
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
)
from pydantic_core import PydanticCustomError


class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        from_attributes=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class ApiStatus(StrEnum):
    UNKNOWN = "unknown"
    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_ERROR = "auth_error"
    INVALID_RESPONSE = "invalid_response"
    IDENTITY_MISMATCH = "identity_mismatch"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


def _utc_input(value: Any) -> Any:
    if isinstance(value, str) and not value.endswith("Z"):
        raise ValueError("Wire timestamps must use UTC Z")
    if not isinstance(value, (str, datetime)):
        raise ValueError("Expected an aware datetime or RFC 3339 UTC timestamp")
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Naive timestamps are not allowed")
    return value.astimezone(UTC)


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise PydanticCustomError("invalid_timezone", "Unknown IANA timezone") from None
    return value


def _days(value: tuple[int, ...]) -> tuple[int, ...]:
    if len(value) != len(set(value)):
        raise ValueError("Weekdays must be unique")
    return tuple(sorted(value))


def _display_name(value: str) -> str:
    if not value.strip():
        raise ValueError("Display name must not be blank")
    return value


UtcDateTime = Annotated[
    datetime,
    BeforeValidator(_utc_input),
    AfterValidator(_aware_utc),
    Field(json_schema_extra={"pattern": "Z$"}),
]
NonEmpty = Annotated[str, Field(min_length=1, strict=True)]
DisplayName = Annotated[
    str,
    Field(min_length=1, max_length=80, strict=True),
    AfterValidator(_display_name),
]
ConnectionRef = Annotated[str, Field(pattern="^[a-z][a-z0-9_-]{0,63}$", strict=True)]
SyncthingFolderId = Annotated[str, Field(min_length=1, max_length=255, strict=True)]
SessionId = Annotated[str, Field(pattern="^[0-9a-f]{64}$", strict=True)]
NonNegative = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[StrictInt, Field(ge=1)]
Ratio = Annotated[float, Field(ge=0, le=1, strict=True)]
IanaTimezone = Annotated[
    str, Field(min_length=1, max_length=64, strict=True), AfterValidator(_timezone)
]
HHMM = Annotated[str, Field(pattern="^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", strict=True)]
Days = Annotated[
    tuple[Annotated[StrictInt, Field(ge=1, le=7)], ...],
    Field(min_length=1, max_length=7, json_schema_extra={"uniqueItems": True}),
    AfterValidator(_days),
]


_DAYS_ADAPTER = TypeAdapter(Days)


def days_to_mask(days: Days) -> int:
    # Public helpers validate too, so B need not instantiate a request to use them.
    checked = _DAYS_ADAPTER.validate_python(days)
    return sum(1 << (day - 1) for day in checked)


def mask_to_days(mask: int) -> tuple[int, ...]:
    if type(mask) is not int or not 1 <= mask <= 127:
        raise ValueError("Days mask must be an integer from 1 to 127")
    return tuple(day for day in range(1, 8) if mask & (1 << (day - 1)))


def hhmm_to_time(value: str) -> time:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None
    ):
        raise ValueError("Expected HH:MM")
    return time.fromisoformat(value)


def time_to_hhmm(value: time) -> str:
    if value.tzinfo is not None or value.second or value.microsecond:
        raise ValueError("Expected a local time with minute precision")
    return value.strftime("%H:%M")
