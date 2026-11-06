from dataclasses import dataclass
from datetime import datetime
from unittest import TestCase

from app.scheduling.engine import calculate_desired_paused


@dataclass(frozen=True)
class Rule:
    timezone: str = "Europe/Moscow"
    days_of_week: tuple[int, ...] = (5,)
    start_time: str = "23:00"
    end_time: str = "02:00"
    enabled: bool = True


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ScheduleEngineTests(TestCase):
    def test_absent_or_disabled_rule_has_no_desired_state(self) -> None:
        assert calculate_desired_paused(None, utc("2026-10-23T20:00:00Z")) is None
        assert (
            calculate_desired_paused(Rule(enabled=False), utc("2026-10-23T20:00:00Z"))
            is None
        )

    def test_same_day_window_includes_start_and_excludes_end(self) -> None:
        rule = Rule(days_of_week=(1,), start_time="09:00", end_time="17:00")
        assert calculate_desired_paused(rule, utc("2026-10-26T06:00:00Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-26T11:59:59Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-26T14:00:00Z")) is True
        assert calculate_desired_paused(rule, utc("2026-10-26T15:00:00Z")) is True

    def test_window_can_continue_into_the_next_local_day(self) -> None:
        rule = Rule(days_of_week=(5,))
        assert calculate_desired_paused(rule, utc("2026-10-23T20:00:00Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-23T21:59:59Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-23T19:59:00Z")) is True
        assert calculate_desired_paused(rule, utc("2026-10-23T23:00:00Z")) is True

    def test_sunday_window_wraps_to_monday(self) -> None:
        rule = Rule(days_of_week=(7,))
        assert calculate_desired_paused(rule, utc("2026-10-25T20:00:00Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-25T22:59:59Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-25T23:00:00Z")) is True

    def test_repeated_dst_hour_has_the_same_result(self) -> None:
        rule = Rule(
            timezone="Europe/Berlin",
            days_of_week=(7,),
            start_time="02:00",
            end_time="03:00",
        )
        assert calculate_desired_paused(rule, utc("2026-10-25T00:30:00Z")) is False
        assert calculate_desired_paused(rule, utc("2026-10-25T01:30:00Z")) is False

    def test_missing_dst_hour_is_not_replayed(self) -> None:
        rule = Rule(
            timezone="Europe/Berlin",
            days_of_week=(7,),
            start_time="02:00",
            end_time="03:00",
        )
        assert calculate_desired_paused(rule, utc("2026-03-29T00:59:00Z")) is True
        assert calculate_desired_paused(rule, utc("2026-03-29T01:00:00Z")) is True

    def test_invalid_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            calculate_desired_paused(
                Rule(timezone="Mars/Phobos"), utc("2026-10-23T20:00:00Z")
            )
        with self.assertRaises(ValueError):
            calculate_desired_paused(
                Rule(start_time="23:00", end_time="23:00"), utc("2026-10-23T20:00:00Z")
            )
        with self.assertRaises(ValueError):
            calculate_desired_paused(
                Rule(days_of_week=(0,)), utc("2026-10-23T20:00:00Z")
            )
        with self.assertRaises(ValueError):
            calculate_desired_paused(Rule(), datetime(2026, 10, 23, 20, tzinfo=None))
