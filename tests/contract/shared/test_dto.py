"""Wire compatibility and invalid-state examples, independent of a database."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.contracts import WIRE_TYPES
from app.contracts.dto import Analytics, NodePatch, RuleCreate, RulePatch
from app.contracts.types import days_to_mask, hhmm_to_time, mask_to_days, time_to_hhmm
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[3]
SPEC = json.loads((ROOT / "docs/design/a/openapi.json").read_text(encoding="utf-8"))
EXAMPLES = json.loads((ROOT / "docs/design/a/contract-examples.json").read_text(encoding="utf-8"))
TYPES = {item.__name__: item for item in WIRE_TYPES}


def fixture_value(name):
    return copy.deepcopy(
        next(case["value"] for case in EXAMPLES["positive"] if case["name"] == name)
    )


def assert_wire(name, value):
    schema = {"$ref": "#/components/schemas/" + name, "components": SPEC["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


@pytest.mark.parametrize("case", EXAMPLES["positive"], ids=lambda case: case["name"])
def test_examples_validate_and_serialize_to_the_published_contract(case):
    adapter = TypeAdapter(TYPES[case["schema"]])
    dto = adapter.validate_json(json.dumps(case["value"]))
    output = adapter.dump_python(dto, mode="json", by_alias=True)
    assert_wire(case["schema"], output)
    assert output == case["value"]


@pytest.mark.parametrize("case", EXAMPLES["negative_schema"], ids=lambda case: case["name"])
def test_invalid_wire_examples_are_rejected(case):
    with pytest.raises(ValidationError):
        TypeAdapter(TYPES[case["schema"]]).validate_json(json.dumps(case["value"]))


@pytest.mark.parametrize("case", EXAMPLES["business_rejections"], ids=lambda case: case["name"])
def test_business_rejections_have_safe_specific_codes(case):
    with pytest.raises(ValidationError) as error:
        TypeAdapter(TYPES[case["schema"]]).validate_json(json.dumps(case["value"]))
    assert any(issue["type"] == case["error_code"] for issue in error.value.errors())


def expand(schema, definitions):
    if isinstance(schema, list):
        return [expand(value, definitions) for value in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return expand(definitions[schema["$ref"].split("/")[-1]], definitions)
    # Descriptive hints (including defaults on required response fields) are not
    # validation constraints. SecretStr's password format is a UI hint too.
    return {
        key: sorted(value) if key == "required" else expand(value, definitions)
        for key, value in schema.items()
        if key not in {"title", "description", "default", "$defs"}
        and not (key == "format" and value == "password")
        and not (key == "required" and not value)
    }


@pytest.mark.parametrize("name", sorted(TYPES))
def test_all_30_wire_shapes_match_the_openapi_schema(name):
    actual = TypeAdapter(TYPES[name]).json_schema(mode="validation")
    expected = SPEC["components"]["schemas"][name]
    assert expand(actual, actual.get("$defs", {})) == expand(
        expected, SPEC["components"]["schemas"]
    )


def test_wire_registry_covers_every_published_type():
    assert set(TYPES) == set(SPEC["components"]["schemas"])


@pytest.mark.parametrize(
    "patch_type,payload",
    [
        (NodePatch, {}),
        (NodePatch, {"enabled": None}),
        (NodePatch, {"enabled": "false"}),
        (NodePatch, {"display_name": "   "}),
        (RulePatch, {"expected_version": 1, "enabled": None}),
        (RulePatch, {"expected_version": 1, "days_of_week": None}),
        (RulePatch, {"expected_version": True, "enabled": False}),
    ],
)
def test_patch_omission_null_and_boolean_are_distinct(patch_type, payload):
    with pytest.raises(ValidationError):
        patch_type.model_validate(payload)


def test_partial_rule_patch_validates_the_complete_merged_window():
    current = RuleCreate.model_validate(fixture_value("overnight_friday"))
    patch = RulePatch(expected_version=1, end_time="23:00")
    assert patch.changes() == {"end_time": "23:00"}
    with pytest.raises(ValidationError) as error:
        patch.apply_to(current)
    assert error.value.errors()[0]["type"] == "invalid_window"
    changed = RulePatch(expected_version=1, enabled=False).apply_to(current)
    assert changed.enabled is False and changed.start_time == current.start_time


def test_weekdays_are_unique_sorted_and_match_all_seven_mask_bits():
    payload = fixture_value("overnight_friday") | {"days_of_week": [7, 1, 5]}
    rule = RuleCreate.model_validate(payload)
    assert rule.days_of_week == (1, 5, 7)
    assert days_to_mask(rule.days_of_week) == 81
    assert mask_to_days(127) == (1, 2, 3, 4, 5, 6, 7)
    assert mask_to_days(81) == rule.days_of_week
    for invalid in [0, 128, True]:
        with pytest.raises(ValueError):
            mask_to_days(invalid)
    assert time_to_hhmm(hhmm_to_time("02:00")) == "02:00"
    for invalid in ["24:00", "02:00:00", "2:00"]:
        with pytest.raises(ValueError):
            hhmm_to_time(invalid)


@pytest.mark.parametrize(
    "changes",
    [
        {"sent_at": None},
        {"verified_at": None},
        {"restart_required": None},
        {"restart_required": True},
        {"observed_paused": None},
        {"observed_paused": False},
        {"source_session_id": None},
        {"completed_at": None},
        {"rule_version": 2},
        {"folder_id": "11111111-1111-4111-8111-111111111111"},
        {"verified_at": "2026-10-24T11:59:59Z"},
    ],
)
def test_a_successful_execution_cannot_hide_incomplete_or_mismatched_evidence(changes):
    with pytest.raises(ValidationError):
        TYPES["Execution"].model_validate(fixture_value("confirmed_configuration") | changes)


@pytest.mark.parametrize(
    "name,changes",
    [
        ("manual_pending", {"desired_paused": True}),
        ("cancelled_before_send", {"sent_at": "2026-10-24T12:00:00Z"}),
        ("manual_pending", {"completed_at": "2026-10-24T12:00:00Z"}),
        ("unknown_after_send", {"error_code": None}),
    ],
)
def test_other_execution_states_remain_distinct(name, changes):
    with pytest.raises(ValidationError):
        TYPES["Execution"].model_validate(fixture_value(name) | changes)


def test_naive_python_times_are_rejected_and_aware_times_serialize_as_utc():
    value = fixture_value("rule_v1")
    with pytest.raises(ValidationError):
        TYPES["Rule"].model_validate(value | {"created_at": datetime(2026, 10, 24)})
    aware = datetime.fromisoformat("2026-10-24T15:00:00+03:00")
    result = TYPES["Rule"].model_validate(value | {"created_at": aware})
    assert result.created_at.tzinfo is UTC
    assert result.model_dump(mode="json")["created_at"] == "2026-10-24T12:00:00Z"


@pytest.mark.parametrize(
    "name,changes",
    [
        ("unknown_folder_state", {"actual_paused": False}),
        ("failed_node_probe", {"need_bytes": 0}),
        ("failed_node_probe", {"success": True}),
        ("sequence_gap", {"source_event_id": 1}),
        ("sequence_gap", {"payload_summary": {"from_id": 44, "to_id": 41}}),
        ("sequence_gap", {"payload_summary": {"path": "private-path-canary"}}),
    ],
)
def test_unknown_observations_and_local_event_markers_are_not_fabricated(name, changes):
    schema = next(case["schema"] for case in EXAMPLES["positive"] if case["name"] == name)
    with pytest.raises(ValidationError):
        TYPES[schema].model_validate(fixture_value(name) | changes)


def test_metrics_do_not_turn_empty_denominators_or_unknown_commands_into_success():
    for name, payload in [
        ("ApiMetrics", {"successful_checks": 0, "total_checks": 0, "success_ratio": 0}),
        ("ApiMetrics", {"successful_checks": 2, "total_checks": 1, "success_ratio": 1}),
        (
            "CommandMetrics",
            {
                "applied": 1,
                "failed": 1,
                "unknown": 2,
                "cancelled": 0,
                "pending_created": 0,
                "success_ratio": 0.25,
            },
        ),
    ]:
        with pytest.raises(ValidationError):
            TYPES[name].model_validate(payload)


def test_a_31_day_period_may_touch_32_utc_dates_but_must_include_every_date():
    value = fixture_value("empty_period")
    start = datetime(2026, 10, 1, 12, tzinfo=UTC)
    value["from"] = start
    value["to"] = start + timedelta(days=31)
    prototype = value["daily"][0]
    value["daily"] = [prototype | {"date": (start + timedelta(days=i)).date()} for i in range(32)]
    assert len(Analytics.model_validate(value).daily) == 32
    with pytest.raises(ValidationError):
        Analytics.model_validate(value | {"daily": value["daily"][:-1]})
    with pytest.raises(ValidationError) as error:
        Analytics.model_validate(value | {"to": start + timedelta(days=31, microseconds=1)})
    assert any(issue["type"] == "invalid_period" for issue in error.value.errors())
