from app.contracts.interfaces import AdapterError
from app.contracts.types import ExecutionStatus
from app.reconciliation.decisions import classify_confirmation

SESSION = "a" * 64


def test_http_success_without_fresh_observation_is_unknown() -> None:
    result = classify_confirmation(
        desired_paused=True,
        observed_paused=None,
        restart_required=None,
        expected_source_session=SESSION,
        observed_source_session=None,
    )
    assert result.status is ExecutionStatus.UNKNOWN
    assert result.error_code == "source_session_unknown"


def test_matching_fresh_observation_is_applied() -> None:
    result = classify_confirmation(
        desired_paused=False,
        observed_paused=False,
        restart_required=False,
        expected_source_session=SESSION,
        observed_source_session=SESSION,
    )
    assert result.status is ExecutionStatus.APPLIED
    assert result.verified is True


def test_mismatch_is_a_definite_failure() -> None:
    result = classify_confirmation(
        desired_paused=True,
        observed_paused=False,
        restart_required=False,
        expected_source_session=SESSION,
        observed_source_session=SESSION,
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "state_mismatch"


def test_unknown_write_can_be_confirmed_by_a_fresh_read() -> None:
    result = classify_confirmation(
        desired_paused=True,
        observed_paused=True,
        restart_required=False,
        expected_source_session=SESSION,
        observed_source_session=SESSION,
        write_error=AdapterError("timeout", operation="write", write_outcome="unknown"),
    )
    assert result.status is ExecutionStatus.APPLIED


def test_not_applied_write_never_becomes_applied() -> None:
    result = classify_confirmation(
        desired_paused=True,
        observed_paused=True,
        restart_required=False,
        expected_source_session=SESSION,
        observed_source_session=SESSION,
        write_error=AdapterError("unreachable", operation="write", write_outcome="not_applied"),
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "unreachable"


def test_source_restart_is_unknown_even_when_value_matches() -> None:
    result = classify_confirmation(
        desired_paused=True,
        observed_paused=True,
        restart_required=False,
        expected_source_session=SESSION,
        observed_source_session="b" * 64,
    )
    assert result.status is ExecutionStatus.UNKNOWN
    assert result.error_code == "source_session_changed"
