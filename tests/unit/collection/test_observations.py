from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.collection.observations import ProbePoint, capture
from app.contracts.dto import EventPayload
from app.contracts.interfaces import (
    FolderConfig,
    FolderRuntime,
    NodeConnection,
    SourceEvent,
    StreamAnchor,
    SystemObservation,
)


@pytest.fixture
def context():
    now = datetime(2026, 10, 26, tzinfo=UTC)
    node = uuid4()
    event = SourceEvent(
        id=1,
        global_id=None,
        type="Starting",
        time_raw="2026-10-26T00:00:00Z",
        syncthing_folder_id=None,
        payload_summary=EventPayload(),
    )
    system = SystemObservation.from_source(
        node_id=node,
        device_id="device-a",
        start_time_raw=event.time_raw,
        observed_at=now,
        stream_anchor=StreamAnchor.from_event(event),
    )
    config = FolderConfig(
        syncthing_folder_id="shared", label="Shared", paused=True, observed_at=now
    )
    client = SimpleNamespace(
        read_system=lambda: system,
        list_folders=lambda: [config],
        read_runtime=lambda _: FolderRuntime(
            state="idle", need_bytes=0, pull_errors=0, observed_at=now
        ),
    )
    point = ProbePoint(
        node,
        "node-a",
        "device-a",
        NodeConnection(
            node_id=node, base_url="http://127.0.0.1:1", api_key="public-observation-key-canary"
        ),
    )
    return client, point, SimpleNamespace(now_utc=lambda: now)


@pytest.mark.parametrize("phase", ["read_system", "list_folders", "read_runtime"])
def test_unexpected_exception_does_not_erase_completed_measurements(context, phase):
    client, point, clock = context

    def failure(*_):
        raise RuntimeError("public-private-path-canary")

    setattr(client, phase, failure)
    result = capture(client, point, clock)
    if phase == "read_system":
        assert result.node_error == "internal_error" and result.configs is None
    elif phase == "list_folders":
        assert result.system is not None and result.config_error == "internal_error"
    else:
        assert result.system is not None and result.configs is not None
        assert result.runtime["shared"][1] == "internal_error"
    assert "canary" not in str(result)


def test_invalid_config_collection_is_not_a_successful_empty_list(context):
    client, point, clock = context
    client.list_folders = lambda: None
    result = capture(client, point, clock)
    assert result.config_error == "invalid_response" and result.configs is None


def test_duplicate_configuration_is_rejected_as_a_whole(context):
    client, point, clock = context
    values = client.list_folders()
    client.list_folders = lambda: values * 2
    assert capture(client, point, clock).config_error == "invalid_response"
