"""Pure command-confirmation decisions used by the worker and its tests."""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.interfaces import AdapterError
from app.contracts.types import ExecutionStatus


@dataclass(frozen=True)
class Confirmation:
    status: ExecutionStatus
    error_code: str | None
    verified: bool


def classify_confirmation(
    *,
    desired_paused: bool,
    observed_paused: bool | None,
    restart_required: bool | None,
    expected_source_session: str | None,
    observed_source_session: str | None,
    write_error: AdapterError | None = None,
) -> Confirmation:
    """Classify a command only from a fresh post-command observation.

    A successful HTTP/write return is intentionally not an ``applied`` result.
    The source epoch, folder value and restart flag must all be observed again.
    A definite adapter ``not_applied`` outcome remains failed even if a later
    read happens to find the desired value; the command itself was not applied.
    """

    if write_error is not None and write_error.write_outcome == "not_applied":
        return Confirmation(ExecutionStatus.FAILED, write_error.code.value, False)
    if write_error is not None and write_error.write_outcome is None:
        return Confirmation(ExecutionStatus.UNKNOWN, write_error.code.value, False)
    if expected_source_session is None or observed_source_session is None:
        return Confirmation(ExecutionStatus.UNKNOWN, "source_session_unknown", False)
    if expected_source_session != observed_source_session:
        return Confirmation(ExecutionStatus.UNKNOWN, "source_session_changed", False)
    if write_error is not None and write_error.write_outcome == "unknown":
        if observed_paused == desired_paused and restart_required is False:
            return Confirmation(ExecutionStatus.APPLIED, None, True)
        return Confirmation(ExecutionStatus.UNKNOWN, write_error.code.value, False)
    if observed_paused is None or restart_required is None:
        return Confirmation(ExecutionStatus.UNKNOWN, "verification_incomplete", False)
    if restart_required:
        return Confirmation(ExecutionStatus.UNKNOWN, "restart_required", False)
    if observed_paused == desired_paused:
        return Confirmation(ExecutionStatus.APPLIED, None, True)
    return Confirmation(ExecutionStatus.FAILED, "state_mismatch", True)
