from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.contracts.dto import EventPayload, Login
from app.contracts.interfaces import (
    AdapterError,
    AdapterErrorCode,
    Clock,
    EventBatch,
    NodeConnection,
    SourceEvent,
    SystemClock,
    SystemObservation,
    source_session_id,
)
from pydantic import ValidationError

NODE = UUID("11111111-1111-4111-8111-111111111111")
RAW = "2026-10-24T12:00:00.123456789Z"
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def test_source_session_preserves_raw_precision_and_separates_nodes():
    observation = SystemObservation.from_source(
        node_id=NODE, device_id="DEMO-DEVICE", start_time_raw=RAW, observed_at=NOW
    )
    assert observation.start_time_raw == RAW
    assert (
        observation.source_session_id
        == "2c717fafafa6d8c916f5be94a9eedc01a0c4aeb1f7006bb0b2591c2642672911"
    )
    assert source_session_id(uuid4(), RAW) != observation.source_session_id
    assert source_session_id(NODE, RAW.replace("789Z", "780Z")) != observation.source_session_id
    assert source_session_id(NODE, RAW.replace("Z", "+00:00")) != observation.source_session_id
    with pytest.raises(TypeError):
        source_session_id(str(NODE), RAW)
    with pytest.raises(ValueError):
        source_session_id(NODE, "not-a-date")


def test_source_event_position_is_distinct_from_global_id_and_payload_is_bounded():
    event = SourceEvent(
        id=4,
        global_id=91,
        type="StateChanged",
        time_raw=RAW,
        syncthing_folder_id="demo/folder",
        payload_summary=EventPayload(state="idle"),
    )
    batch = EventBatch(source_session_id="a" * 64, events=[event], observed_at=NOW)
    assert batch.events[0].id == 4 and batch.events[0].global_id == 91
    assert batch.events[0].time_raw == RAW
    assert event.model_dump(mode="json")["payload_summary"] == {"state": "idle"}
    with pytest.raises(ValidationError):
        EventBatch(source_session_id="a" * 64, events=[event, event], observed_at=NOW)
    with pytest.raises(ValidationError):
        EventPayload(path="private-path-canary")
    assert EventBatch(source_session_id="a" * 64, events=[], observed_at=NOW).events == ()


def test_connections_and_login_never_expose_secrets_in_repr_or_serialization():
    connection = NodeConnection(
        node_id=NODE, base_url="http://secret-host-canary:8384", api_key="adapter-key-canary"
    )
    assert connection.base_url.get_secret_value() == "http://secret-host-canary:8384"
    login = Login(login="admin", password="login-password-canary")
    for value, secret in [(connection, "adapter-key-canary"), (login, "login-password-canary")]:
        assert secret not in repr(value) + value.model_dump_json()
    assert "secret-host-canary" not in repr(connection) + connection.model_dump_json()
    assert login.password.get_secret_value() == "login-password-canary"
    with pytest.raises(ValidationError) as error:
        NodeConnection(node_id=NODE, base_url="http://user:password-canary@host", api_key="key")
    assert "password-canary" not in str(error.value)
    with pytest.raises(ValidationError) as error:
        Login(login="admin", password="long-password-canary" * 100)
    assert "long-password-canary" not in str(error.value)


@pytest.mark.parametrize("code", list(AdapterErrorCode))
def test_adapter_write_errors_are_unknown_unless_explicitly_classified(code):
    read_error = AdapterError(code)
    write_error = AdapterError(code, operation="write")
    assert read_error.write_outcome is None
    assert write_error.write_outcome == "unknown"
    assert read_error.retryable == (code in {"timeout", "unreachable", "session_changed"})
    assert str(write_error) == f"Syncthing operation failed: {code.value}"


def test_adapter_error_has_no_raw_message_argument_and_rejects_invalid_codes_safely():
    assert (
        AdapterError("auth_error", operation="write", write_outcome="not_applied").write_outcome
        == "not_applied"
    )
    with pytest.raises(TypeError):
        AdapterError("timeout", message="upstream-secret-canary")
    with pytest.raises(ValueError) as error:
        AdapterError("upstream-secret-canary")
    assert "upstream-secret-canary" not in str(error.value)
    with pytest.raises(ValueError):
        AdapterError("timeout", write_outcome="unknown")


def test_clock_returns_aware_utc_and_accepts_a_fake_without_database_or_network():
    class FakeClock:
        def now_utc(self):
            return NOW

    assert isinstance(FakeClock(), Clock)
    assert FakeClock().now_utc() == NOW
    assert SystemClock().now_utc().tzinfo is UTC
