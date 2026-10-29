"""Adapter semantics on captured shapes, with no network or working Syncthing."""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from app.contracts.interfaces import (
    AdapterError,
    AdapterFactory,
    NodeConnection,
    SourceCheckpoint,
    SyncthingAdapter,
)
from app.syncthing_adapter import HttpAdapterFactory, HttpSyncthingAdapter, ResolvedNode
from app.syncthing_adapter.transport import Response

FIXTURES = Path(__file__).parent / "fixtures/v2.1.7"
NODE = UUID("11111111-1111-4111-8111-111111111111")
DEVICE = "DEMO-EXPECTED-DEVICE"
NOW = datetime(2026, 10, 25, tzinfo=UTC)


class Clock:
    def now_utc(self):
        return NOW


def connection(key="unit-secret-key"):
    return NodeConnection(node_id=NODE, base_url="http://private.invalid:18384", api_key=key)


def event(position, *, state="idle", time="2026-10-25T00:00:00.123456789+03:00"):
    return {
        "id": position,
        "globalID": position + 100,
        "type": "StateChanged",
        "time": time,
        "data": {"folder": "scc-a1-shared", "to": state, "path": "private-file-name"},
    }


class Source:
    def __init__(self):
        self.status = {
            "myID": DEVICE,
            "startTime": "2026-10-24T23:46:48.123456789+03:00",
            "uptime": 10,
        }
        self.folders = json.loads((FIXTURES / "node-a-folders.json").read_text(encoding="utf-8"))
        self.folders.sort(key=lambda item: item["id"] != "scc-a1-shared")
        self.runtime = {"state": "idle", "needBytes": 0, "pullErrors": 0}
        self.events = [event(1), event(3)]
        self.requires_restart = False
        self.calls = []
        self.closed = False
        self.override = None
        self.on_poll = None
        self.patch_error = None

    def request(self, method, path, *, body=None, timeout=5):
        self.calls.append((method, path, body, timeout))
        if self.closed:
            raise AdapterError("unreachable")
        if self.override is not None:
            return self.override(method, path)
        if method == "PATCH":
            if self.patch_error is not None:
                raise self.patch_error
            return Response(b"", "", self.status["myID"])
        url = urlsplit(path)
        if url.path == "/rest/system/status":
            value = self.status
        elif url.path == "/rest/config/folders":
            value = self.folders
        elif url.path.startswith("/rest/config/folders/"):
            value = self.folders[0]
        elif url.path == "/rest/db/status":
            value = self.runtime
        elif url.path == "/rest/config/restart-required":
            value = {"requiresRestart": self.requires_restart}
        elif url.path == "/rest/events":
            query = parse_qs(url.query)
            if query.get("limit") == ["1"]:
                value = self.events[-1:]
            else:
                value = [item for item in self.events if item["id"] > int(query["since"][0])]
                if self.on_poll is not None:
                    self.on_poll()
        else:
            raise AssertionError("Unexpected endpoint")
        return Response(
            json.dumps(value).encode(), "application/json; charset=utf-8", self.status["myID"]
        )

    def close(self):
        self.closed = True


@pytest.fixture
def source():
    return Source()


@pytest.fixture
def adapter(source):
    return HttpSyncthingAdapter(
        connection(), expected_device_id=DEVICE, clock=Clock(), transport=source
    )


def test_read_methods_return_a0_observations(adapter):
    assert isinstance(adapter, SyncthingAdapter)
    system = adapter.read_system()
    assert system.observed_at == NOW
    assert system.start_time_raw.endswith(".123456789+03:00")
    assert len(system.source_session_id) == 64
    assert len(adapter.list_folders()) == 2
    assert adapter.read_folder("scc-a1-shared").paused is False
    runtime = adapter.read_runtime("scc-a1-shared")
    assert runtime.need_bytes == 0 and runtime.pull_errors == 0
    assert adapter.restart_required() is False


def test_unknown_runtime_and_absent_counters_remain_unknown(adapter, source):
    source.runtime = {"state": "future-state"}
    observed = adapter.read_runtime("scc-a1-shared")
    assert observed.state == "future-state"
    assert observed.need_bytes is None and observed.pull_errors is None


def test_empty_runtime_state_seen_on_a_paused_source_is_not_invented_as_idle(adapter, source):
    source.runtime = {"state": "", "needBytes": 0, "pullErrors": 0}
    with pytest.raises(AdapterError, match="invalid_response"):
        adapter.read_runtime("scc-a1-shared")


@pytest.mark.parametrize(
    "label,expected", [("", "scc-a1-shared"), (" " * 4, "scc-a1-shared"), ("a" * 100, "a" * 80)]
)
def test_source_label_maps_to_a0_display_name(adapter, source, label, expected):
    source.folders[0]["label"] = label
    assert adapter.read_folder("scc-a1-shared").label == expected


@pytest.mark.parametrize(
    "change,operation",
    [
        (lambda s: s.runtime.update(needBytes=True), "runtime"),
        (lambda s: s.runtime.update(pullErrors=-1), "runtime"),
        (lambda s: s.runtime.update(state=None), "runtime"),
        (lambda s: s.folders[0].update(paused="false"), "folders"),
        (lambda s: s.folders.append(s.folders[0]), "folders"),
        (lambda s: setattr(s, "requires_restart", 0), "restart"),
        (lambda s: s.status.update(uptime=False), "system"),
        (lambda s: s.status.update(startTime="not-a-timestamp"), "system"),
    ],
)
def test_malformed_observations_are_not_coerced(adapter, source, change, operation):
    change(source)
    method = {
        "runtime": lambda: adapter.read_runtime("scc-a1-shared"),
        "folders": adapter.list_folders,
        "restart": adapter.restart_required,
        "system": adapter.read_system,
    }[operation]
    with pytest.raises(AdapterError, match="invalid_response"):
        method()


@pytest.mark.parametrize(
    "body,kind",
    [
        (b'{"myID":"x","myID":"y"}', "application/json"),
        (b'{"x":NaN}', "application/json"),
        (b"{}", "text/html"),
        (b"\xff", "application/json"),
        (b"{invalid secret-body}", "application/json"),
    ],
)
def test_invalid_json_is_safe(adapter, source, body, kind):
    source.override = lambda *_: Response(body, kind)
    with pytest.raises(AdapterError) as caught:
        adapter.read_system()
    assert caught.value.code == "invalid_response"
    assert "secret-body" not in str(caught.value) + repr(caught.value)


def test_discovery_pins_valid_identity_and_detects_a_changed_source(source):
    client = HttpSyncthingAdapter(connection(), transport=source)
    assert client.read_system().device_id == DEVICE
    source.status["myID"] = "OTHER-DEVICE"
    with pytest.raises(AdapterError, match="identity_mismatch"):
        client.list_folders()


def test_discovery_rejects_disagreement_between_header_and_body(source):
    source.override = lambda *_: Response(
        json.dumps(source.status).encode(), "application/json", "OTHER"
    )
    client = HttpSyncthingAdapter(connection(), transport=source)
    with pytest.raises(AdapterError, match="identity_mismatch"):
        client.read_system()


def test_wrong_node_preflight_prevents_any_patch(adapter, source):
    source.status["myID"] = "WRONG-NODE"
    with pytest.raises(AdapterError) as caught:
        adapter.set_paused("scc-a1-shared", True)
    assert caught.value.code == "identity_mismatch" and caught.value.write_outcome == "not_applied"
    assert all(call[0] == "GET" for call in source.calls)


def test_folder_id_is_one_encoded_segment_and_query_parameter(adapter, source):
    folder_id = "folder/с пробелом?x=1#fragment"
    source.folders[0]["id"] = folder_id
    assert adapter.read_folder(folder_id).syncthing_folder_id == folder_id
    adapter.read_runtime(folder_id)
    adapter.set_paused(folder_id, False)
    config_paths = [call[1] for call in source.calls if call[1].startswith("/rest/config/folders/")]
    assert len(config_paths) == 2
    assert all(
        "%2F" in path and "%3F" in path and "%23" in path and "?" not in path
        for path in config_paths
    )
    runtime_path = next(call[1] for call in source.calls if call[1].startswith("/rest/db/status?"))
    assert parse_qs(urlsplit(runtime_path).query) == {"folder": [folder_id]}


def test_folder_response_cannot_substitute_another_id(adapter):
    with pytest.raises(AdapterError, match="invalid_response"):
        adapter.read_folder("different-folder")


@pytest.mark.parametrize(
    "folder_id,paused",
    [("..", True), ("", False), (True, True), ("scc-a1-shared", 1), ("scc-a1-shared", "false")],
)
def test_invalid_command_never_reaches_the_network(adapter, source, folder_id, paused):
    with pytest.raises(AdapterError) as caught:
        adapter.set_paused(folder_id, paused)
    assert caught.value.write_outcome == "not_applied"
    assert source.calls == []


def test_patch_is_single_field_single_attempt_without_confirmation_or_restart(adapter, source):
    assert adapter.set_paused("scc-a1-shared", True) is None
    assert [call[0] for call in source.calls] == ["GET", "GET", "GET", "PATCH"]
    patch = source.calls[-1]
    assert patch[1] == "/rest/config/folders/scc-a1-shared"
    assert json.loads(patch[2]) == {"paused": True}
    assert patch[3] == 5
    source.requires_restart = True
    assert adapter.restart_required() is True


@pytest.mark.parametrize(
    "code,outcome",
    [
        ("timeout", "unknown"),
        ("unreachable", "unknown"),
        ("invalid_response", "unknown"),
        ("auth_error", "not_applied"),
        ("not_found", "not_applied"),
    ],
)
def test_write_outcome_is_preserved_without_retry(adapter, source, code, outcome):
    source.patch_error = AdapterError(code, operation="write", write_outcome=outcome)
    with pytest.raises(AdapterError) as caught:
        adapter.set_paused("scc-a1-shared", False)
    assert caught.value.code == code and caught.value.write_outcome == outcome
    assert sum(call[0] == "PATCH" for call in source.calls) == 1


def test_events_preserve_stream_ids_source_time_and_allow_gaps(adapter, source):
    batch = adapter.read_events(0)
    assert [item.id for item in batch.events] == [1, 3]
    assert [item.global_id for item in batch.events] == [101, 103]
    assert batch.events[0].time_raw.endswith(".123456789+03:00")
    assert batch.events[0].payload_summary.state == "idle"
    assert "private-file-name" not in batch.model_dump_json()
    assert batch.source_session_id == adapter.read_system().source_session_id
    poll = next(call for call in source.calls if call[1] == "/rest/events?since=0&timeout=15")
    assert poll[3] == 20
    assert all("events=" not in call[1] and "/disk" not in call[1] for call in source.calls)


def test_empty_event_batch_is_valid(adapter):
    adapter.read_events(0, timeout_seconds=0)
    batch = adapter.read_events(3, timeout_seconds=0)
    assert batch.events == ()


def test_arbitrary_payload_and_path_like_state_are_dropped(adapter, source):
    source.events = [event(1, state="C:/Users/private/file")]
    source.events[0]["data"]["apikey"] = "private-key"
    batch = adapter.read_events(0, 0)
    assert batch.events[0].payload_summary.model_dump() == {}
    assert "private" not in batch.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        lambda s: s.events.reverse(),
        lambda s: s.events.append(s.events[-1]),
        lambda s: s.events[0].update(id=True),
        lambda s: s.events[0].update(globalID=-1),
        lambda s: s.events[0].update(time="invalid"),
    ],
)
def test_invalid_event_batches_are_rejected(adapter, source, changes):
    changes(source)
    with pytest.raises(AdapterError, match="invalid_response"):
        adapter.read_events(0, 0)


@pytest.mark.parametrize("index", [0, -1])
def test_oversized_stream_positions_are_safe_errors_without_poisoning_the_epoch(
    adapter, source, index
):
    saved_id = source.events[index]["id"]
    source.events[index]["id"] = 2**63
    with pytest.raises(AdapterError, match="invalid_response"):
        adapter.read_system()
    source.events[index]["id"] = saved_id
    assert adapter.read_events(0, 0).events[-1].id == 3


def test_oversized_position_in_the_poll_is_rejected_without_a_checkpoint_advance(
    adapter, source, monkeypatch
):
    initial = adapter.read_events(0, 0)
    original = source.request

    def bad_poll(method, path, **kwargs):
        if path == "/rest/events?since=3&timeout=0":
            return Response(json.dumps([event(2**63)]).encode(), "application/json", DEVICE)
        return original(method, path, **kwargs)

    monkeypatch.setattr(source, "request", bad_poll)
    with pytest.raises(AdapterError, match="invalid_response"):
        adapter.read_events(3, 0)
    assert adapter.read_system().source_session_id == initial.source_session_id
    assert adapter.read_system().stream_anchor == initial.source_checkpoint.anchor


@pytest.mark.parametrize(
    "since,timeout", [(-1, 0), (True, 0), (2**63, 0), (0, -1), (0, 16), (0, True)]
)
def test_event_parameters_are_bounded_before_any_request(adapter, source, since, timeout):
    with pytest.raises(ValueError):
        adapter.read_events(since, timeout)
    assert source.calls == []


def test_restart_between_reads_discards_batch_and_allows_new_session(adapter, source):
    previous = adapter.read_system().source_session_id
    source.on_poll = lambda: source.status.update(startTime="2026-10-25T00:00:02+03:00", uptime=0)
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(0, 0)
    source.on_poll = None
    batch = adapter.read_events(0, 0)
    assert batch.source_session_id != previous


def test_identity_change_during_long_poll_discards_batch(adapter, source):
    source.on_poll = lambda: source.status.update(myID="OTHER-SOURCE")
    with pytest.raises(AdapterError, match="identity_mismatch"):
        adapter.read_events(0, 0)


def test_uptime_regression_changes_the_epoch_instead_of_assuming_continuity(adapter, source):
    previous = adapter.read_system().source_session_id
    source.status["uptime"] = 0
    current = adapter.read_system().source_session_id
    assert current != previous
    source.status["uptime"] = 100
    assert adapter.read_system().source_session_id == current


def test_stream_position_regression_requires_rebase_and_creates_a_distinct_epoch(adapter, source):
    previous = adapter.read_events(0, 0).source_session_id
    source.events = [event(1)]
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(3, 0)
    current = adapter.read_events(0, 0)
    assert current.source_session_id != previous and current.events[0].id == 1


def test_old_cursor_after_normal_restart_does_not_poison_the_new_session(adapter, source):
    adapter.read_events(0, 0)
    source.status.update(startTime="2026-10-25T00:00:02+03:00", uptime=0)
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(100, 0)
    assert adapter.read_events(0, 0).events


def test_factory_refreshes_rotated_credentials_and_closes_owned_clients():
    target = [ResolvedNode(connection(), DEVICE)]
    clients = []

    def transport_factory(_connection):
        client = Source()
        clients.append(client)
        return client

    factory = HttpAdapterFactory(
        lambda _: target[0], clock=Clock(), transport_factory=transport_factory
    )
    assert isinstance(factory, AdapterFactory)
    first = factory.for_node(NODE)
    assert factory.for_node(NODE) is first
    target[0] = ResolvedNode(connection("rotated-private-key"), DEVICE)
    second = factory.for_node(NODE)
    assert first is not second and clients[0].closed
    with pytest.raises(AdapterError, match="unreachable"):
        first.read_system()
    assert "private.invalid" not in repr(target[0]) and "private-key" not in repr(target[0])
    factory.close()
    factory.close()
    assert clients[1].closed
    with pytest.raises(AdapterError, match="unreachable"):
        factory.for_node(NODE)


def test_factory_rejects_resolver_returning_a_different_internal_uuid():
    wrong = NodeConnection(node_id=UUID(int=2), base_url="http://private.invalid", api_key="secret")
    factory = HttpAdapterFactory(lambda _: ResolvedNode(wrong, DEVICE))
    with pytest.raises(ValueError, match="requested node"):
        factory.for_node(NODE)
    with pytest.raises(TypeError, match="internal node UUID"):
        factory.for_node(str(NODE))


def test_resolver_failure_closes_stale_client_and_never_exposes_secret_error_text(source):
    available = [True]

    def resolve(_node):
        if not available[0]:
            raise KeyError("private-url-and-api-key")
        return ResolvedNode(connection(), DEVICE)

    factory = HttpAdapterFactory(resolve, transport_factory=lambda _: source)
    factory.for_node(NODE).read_system()
    available[0] = False
    with pytest.raises(AdapterError) as caught:
        factory.for_node(NODE)
    assert caught.value.code == "not_found"
    assert "private-url" not in repr(caught.value)
    assert source.closed
    factory.close()


def test_same_second_restart_with_counter_already_beyond_cursor_cannot_mix_epochs(adapter, source):
    source.status["startTime"] = "2026-10-25T00:00:00+03:00"
    old = adapter.read_events(0, 0)
    source.events = [
        event(index, time="2026-10-25T00:00:00.987654321+03:00") for index in range(1, 7)
    ]
    # Same startTime, same/increasing uptime, and a new tail higher than the saved cursor.
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(3, 0)
    current = adapter.read_events(0, 0)
    assert current.source_session_id != old.source_session_id
    assert [item.id for item in current.events] == list(range(1, 7))
    assert current.source_checkpoint.start_time_raw == old.source_checkpoint.start_time_raw
    assert adapter.read_events(6, 0).events == ()


def test_checkpoint_detects_changed_event_even_when_id_and_timestamp_are_reused(adapter, source):
    old = adapter.read_events(0, 0)
    source.events[-1]["data"]["to"] = "syncing"
    # The fingerprint includes safe event contents as well as source id/time.
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(3, 0)
    assert adapter.read_events(0, 0).source_session_id != old.source_session_id


def test_recreated_backend_resumes_persisted_checkpoint_when_original_first_event_is_gone(
    adapter, source
):
    old = adapter.read_events(0, 0)
    persisted = SourceCheckpoint.model_validate_json(old.source_checkpoint.model_dump_json())
    source.events = [source.events[-1], event(4)]
    recreated = HttpSyncthingAdapter(
        connection(),
        expected_device_id=DEVICE,
        checkpoint=persisted,
        clock=Clock(),
        transport=source,
    )
    batch = recreated.read_events(3, 0)
    assert batch.source_session_id == old.source_session_id
    assert [item.id for item in batch.events] == [4]
    assert batch.source_checkpoint.anchor.id == 4


def test_lost_checkpoint_starts_new_epoch_without_claiming_a_proven_restart(adapter, source):
    old = adapter.read_events(0, 0)
    source.events = [
        event(4),
        event(5),
    ]  # Normal buffer loss is also insufficient continuity proof.
    recreated = HttpSyncthingAdapter(
        connection(), expected_device_id=DEVICE, checkpoint=old.source_checkpoint, transport=source
    )
    observed = recreated.read_system()
    assert observed.start_time_raw == old.source_checkpoint.start_time_raw
    assert observed.source_session_id != old.source_session_id
    with pytest.raises(AdapterError, match="session_changed"):
        recreated.read_events(3, 0)
    assert [item.id for item in recreated.read_events(0, 0).events] == [4, 5]


def test_legacy_coarse_session_without_anchor_is_not_trusted(source):
    legacy = SourceCheckpoint(
        node_id=NODE, start_time_raw=source.status["startTime"], source_session_id="a" * 64
    )
    client = HttpSyncthingAdapter(connection(), checkpoint=legacy, transport=source)
    with pytest.raises(AdapterError, match="session_changed"):
        client.read_events(3, 0)
    assert client.read_events(0, 0).source_session_id != legacy.source_session_id


def test_checkpoint_for_another_internal_node_is_rejected_before_network(source):
    checkpoint = SourceCheckpoint(
        node_id=UUID(int=2), start_time_raw=source.status["startTime"], source_session_id="a" * 64
    )
    with pytest.raises(ValueError, match="requested node"):
        HttpSyncthingAdapter(connection(), checkpoint=checkpoint, transport=source)
    assert source.calls == []


def test_empty_source_window_cannot_establish_a_session_and_is_retryable(adapter, source):
    source.events = []
    with pytest.raises(AdapterError, match="session_changed") as caught:
        adapter.read_system()
    assert caught.value.retryable
    source.events = [event(1)]
    assert adapter.read_system().stream_anchor.id == 1


def test_same_second_restart_during_long_poll_discards_whole_batch(adapter, source, monkeypatch):
    original_batch = adapter.read_events(0, 0)
    request = source.request

    def restart_on_poll(method, path, **kwargs):
        if path == "/rest/events?since=3&timeout=1":
            source.events = [
                event(index, time="2026-10-25T00:00:00.987654321+03:00") for index in range(1, 7)
            ]
        return request(method, path, **kwargs)

    monkeypatch.setattr(source, "request", restart_on_poll)
    with pytest.raises(AdapterError, match="session_changed"):
        adapter.read_events(3, 1)
    batch = adapter.read_events(0, 0)
    assert batch.source_session_id != original_batch.source_session_id and batch.events[0].id == 1
