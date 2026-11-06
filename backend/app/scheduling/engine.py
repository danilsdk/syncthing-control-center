"""Calculate a folder's desired paused state from a weekly local-time rule."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class RuleLike(Protocol):
    """The fields required from the shared Rule/RuleFields DTO.

    Keeping this boundary structural avoids a dependency from the pure calculator
    on Pydantic while still accepting the shared contract objects at runtime.
    """

    timezone: str
    days_of_week: tuple[int, ...]
    start_time: str
    end_time: str
    enabled: bool


_HHMM = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def _minutes(value: str) -> int:
    if not isinstance(value, str) or _HHMM.fullmatch(value) is None:
        raise ValueError("Expected a local time in HH:MM format")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _validate_days(days: tuple[int, ...]) -> frozenset[int]:
    if not isinstance(days, (tuple, list)) or not days:
        raise ValueError("At least one ISO weekday is required")
    if any(type(day) is not int or not 1 <= day <= 7 for day in days):
        raise ValueError("ISO weekdays must be integers from 1 to 7")
    if len(days) != len(set(days)):
        raise ValueError("Weekdays must be unique")
    return frozenset(days)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown IANA timezone") from None


def calculate_desired_paused(rule: RuleLike | None, now_utc: datetime) -> bool | None:
    """Return the desired ``paused`` state at an explicit aware moment.

    The interval is half-open, ``[start_time, end_time)``. A window whose end is
    earlier than its start continues after local midnight; the selected weekday
    is the weekday on which the window started. A disabled or absent rule returns
    ``None`` so callers cannot accidentally issue a command.
    """

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("The calculation moment must be timezone-aware")
    if rule is None or not rule.enabled:
        return None

    start = _minutes(rule.start_time)
    end = _minutes(rule.end_time)
    if start == end:
        raise ValueError("Window boundaries must differ")
    days = _validate_days(rule.days_of_week)

    local = now_utc.astimezone(_zone(rule.timezone))
    local_minutes = local.hour * 60 + local.minute
    current_day = local.isoweekday()

    if start < end:
        inside = current_day in days and start <= local_minutes < end
    else:
        previous_day = 7 if current_day == 1 else current_day - 1
        inside = (current_day in days and local_minutes >= start) or (
            previous_day in days and local_minutes < end
        )
    return not inside
