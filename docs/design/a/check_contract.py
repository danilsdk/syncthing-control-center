"""Validate design artifacts; does not exercise the unimplemented backend."""

from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def rule_problem(rule):
    if rule["start_time"] == rule["end_time"]:
        return "invalid_window"
    try:
        ZoneInfo(rule.get("timezone", "Europe/Moscow"))
    except ZoneInfoNotFoundError:
        return "invalid_timezone"
    return None


def expected_paused(rule, instant):
    """Reference calculation for the explicit, hand-picked B0 examples."""
    if rule is None or not rule["enabled"]:
        return None
    local = datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(
        ZoneInfo(rule["timezone"])
    )
    clock = local.strftime("%H:%M")
    day = local.isoweekday()
    start, end, days = rule["start_time"], rule["end_time"], rule["days_of_week"]
    if start < end:
        allowed = day in days and start <= clock < end
    else:
        previous = 7 if day == 1 else day - 1
        allowed = (day in days and clock >= start) or (previous in days and clock < end)
    return not allowed


def check_execution(value):
    status = value["status"]
    terminal = status != "pending"
    assert (value["completed_at"] is not None) == terminal
    if status == "pending":
        assert value["verified_at"] is None and value["error_code"] is None
    elif status == "applied":
        assert value["sent_at"] is not None and value["verified_at"] is not None
        assert value["restart_required"] is False and value["error_code"] is None
        assert value["observed_paused"] == value["desired_paused"]
        assert value["source_session_id"] is not None
    else:
        assert value["error_code"] is not None
    if status == "cancelled":
        assert value["sent_at"] is None and value["verified_at"] is None
    if value["source"] == "manual":
        assert value["rule_id"] is None and value["rule_version"] is None
        assert value["rule_snapshot"] is None and value["desired_paused"] is False
    else:
        assert value["rule_snapshot"]["version"] == value["rule_version"]
        assert value["rule_snapshot"]["folder_id"] == value["folder_id"]
    for stamp in ["sent_at", "verified_at", "completed_at"]:
        if value[stamp] is not None:
            assert datetime.fromisoformat(value[stamp]) >= datetime.fromisoformat(value["created_at"])
    if value["sent_at"] is not None and value["verified_at"] is not None:
        assert datetime.fromisoformat(value["verified_at"]) >= datetime.fromisoformat(value["sent_at"])
    if value["completed_at"] is not None:
        for stamp in ["sent_at", "verified_at"]:
            if value[stamp] is not None:
                assert datetime.fromisoformat(value["completed_at"]) >= datetime.fromisoformat(value[stamp])


def check_metrics(value):
    command, api = value["commands"], value["api_checks"]
    denominator = command["applied"] + command["failed"]
    ratio = command["applied"] / denominator if denominator else None
    assert command["success_ratio"] == ratio
    assert api["successful_checks"] <= api["total_checks"]
    ratio = api["successful_checks"] / api["total_checks"] if api["total_checks"] else None
    assert api["success_ratio"] == ratio


def main():
    spec, examples = load("openapi.json"), load("contract-examples.json")
    validate_spec(spec)
    assert spec["info"]["version"] == examples["contract_version"]
    ids = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            ids.append(operation["operationId"])
            if method in ("post", "patch", "delete"):
                assert any(p["name"] == "Origin" and p["required"] for p in operation["parameters"])
            if path.startswith("/api/") and path != "/api/auth/login":
                assert operation.get("security", spec["security"]) == [{"adminSession": []}]
                if method in ("post", "patch", "delete"):
                    assert any(p["name"] == "X-CSRF-Token" and p["required"] for p in operation["parameters"])
    assert len(ids) == len(set(ids))
    def errors(case):
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "$ref": "#/components/schemas/" + case["schema"],
                  "components": spec["components"]}
        return list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(case["value"]))
    for case in examples["positive"]:
        found = errors(case)
        assert not found, (case["name"], [e.message for e in found])
        if case["schema"] == "Execution":
            check_execution(case["value"])
        if case["schema"] == "Analytics":
            check_metrics(case["value"])
            for day in case["value"]["daily"]:
                check_metrics(day)
    for case in examples["negative_schema"]:
        assert errors(case), case["name"]
    for case in examples["business_rejections"]:
        assert not errors(case), "Business examples must pass structural validation"
        assert rule_problem(case["value"]) == case["error_code"], case["name"]
    for case in examples["schedule_cases"]:
        if case["rule"] is not None:
            assert rule_problem(case["rule"]) is None
        assert expected_paused(case["rule"], case["at"]) == case["desired_paused"], case["name"]
    # Structural and navigation checks, including artifacts introduced before A0.1.
    docs = [ROOT / "README.md", ROOT / "docs/contracts.md", ROOT / "docs/agent-log/a/A0.md", *HERE.glob("*.md")]
    for path in docs:
        content = path.read_text(encoding="utf-8")
        assert "\ufffd" not in content
        assert all(line == line.rstrip() for line in content.splitlines()), path
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
    print(f"OK: OpenAPI {spec['openapi']}, {len(ids)} operations, {len(spec['components']['schemas'])} schemas")
    print(f"OK: {len(examples['positive'])} valid DTO examples, {len(examples['negative_schema'])} rejected DTO examples")
    print(f"OK: {len(examples['schedule_cases'])} time cases, {len(examples['business_rejections'])} business rejections, command/metric invariants")
    print(f"OK: UTF-8, whitespace and local links in {len(docs)} documents")
    print("Design checks only. No backend, PostgreSQL or real Syncthing was exercised.")


if __name__ == "__main__":
    main()
