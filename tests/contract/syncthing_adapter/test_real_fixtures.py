"""Verify captured wire shapes against A0; this does not launch a live source."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.contracts.dto import EventPayload
from app.contracts.interfaces import (
    EventBatch,
    FolderConfig,
    FolderRuntime,
    SourceCheckpoint,
    SourceEvent,
    StreamAnchor,
    SystemObservation,
)

FIXTURES = Path(__file__).parent / "fixtures/v2.1.7"
NOW = datetime(2026, 10, 24, 21, tzinfo=UTC)


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["node-a", "node-b"])
def test_captured_success_responses_fit_shared_observations(name):
    node_id = UUID(
        "11111111-1111-4111-8111-111111111111"
        if name == "node-a"
        else "22222222-2222-4222-8222-222222222222"
    )
    status = read(f"{name}-system-status.json")
    first_event = read(f"{name}-events.json")[0]
    anchor = StreamAnchor.from_event(
        SourceEvent(
            id=first_event["id"],
            global_id=first_event.get("globalID"),
            type=first_event["type"],
            time_raw=first_event["time"],
            syncthing_folder_id=None,
            payload_summary=EventPayload(),
        )
    )
    system = SystemObservation.from_source(
        node_id=node_id,
        device_id=status["myID"],
        start_time_raw=status["startTime"],
        observed_at=NOW,
        stream_anchor=anchor,
    )
    assert system.start_time_raw == status["startTime"]
    assert read(f"{name}-system-version.json")["version"] == "v2.1.7"
    configs = read(f"{name}-folders.json")
    assert {item["id"] for item in configs} == {"scc-a1-shared", "scc-a1-control"}
    for item in configs:
        parsed = FolderConfig(
            syncthing_folder_id=item["id"],
            label=item["label"],
            paused=item["paused"],
            observed_at=NOW,
        )
        assert parsed.paused is False
        assert item["path"].startswith("/DEMO/")
        assert item["rescanIntervalS"] == 7200 and item["fsWatcherEnabled"] is False
    runtime = read(f"{name}-folder-runtime.json")
    FolderRuntime(
        state=runtime["state"],
        need_bytes=runtime["needBytes"],
        pull_errors=runtime["pullErrors"],
        observed_at=NOW,
    )
    events = []
    for event in read(f"{name}-events.json"):
        data = event["data"]
        state = data.get("state", data.get("to"))
        payload = EventPayload(state=state) if state is not None else EventPayload()
        events.append(
            SourceEvent(
                id=event["id"],
                global_id=event.get("globalID"),
                type=event["type"],
                time_raw=event["time"],
                syncthing_folder_id=data.get("folder"),
                payload_summary=payload,
            )
        )
    batch = EventBatch(
        source_session_id=system.source_session_id,
        events=events,
        observed_at=NOW,
        source_checkpoint=SourceCheckpoint.from_observation(node_id, system),
    )
    assert batch.events
    assert read(f"{name}-restart-required.json") == {"requiresRestart": False}


def test_two_sources_are_distinct_and_errors_are_real_statuses():
    assert read("node-a-system-status.json")["myID"] != read("node-b-system-status.json")["myID"]
    for name in ("node-a", "node-b"):
        assert read(f"{name}-auth-error.json")["status_code"] == 403
        assert read(f"{name}-folder-not-found.json")["status_code"] == 404


def test_published_fixtures_exclude_credentials_and_personal_paths():
    files = list(FIXTURES.glob("*.json"))
    assert len(files) == 19
    for path in files:
        content = path.read_text(encoding="utf-8")
        json.loads(content)
        assert "apikey" not in content.lower()
        assert "encryptionPassword" not in content
        assert "Users\\\\" not in content and "/Users/" not in content
        assert "syncthing-control-center-a1" not in content
