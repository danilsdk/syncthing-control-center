"""Public wire DTOs. State transitions and database queries remain in owning services."""

import math
from datetime import date, timedelta
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, StrictBool, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from app.contracts.types import (
    DTO,
    HHMM,
    ApiStatus,
    ConnectionRef,
    Days,
    DisplayName,
    ExecutionStatus,
    IanaTimezone,
    NonEmpty,
    NonNegative,
    Positive,
    Ratio,
    SessionId,
    SyncthingFolderId,
    UtcDateTime,
)


def _omitted() -> Any:
    # Omission is tracked with model_fields_set. Explicit null still fails the field type;
    # a factory keeps the JSON schema non-nullable without a misleading default:null.
    return None


class ErrorDetail(DTO):
    field: str
    message: str


class ErrorBody(DTO):
    code: NonEmpty
    message: NonEmpty
    details: tuple[ErrorDetail, ...]
    request_id: UUID


class Error(DTO):
    error: ErrorBody


class User(DTO):
    id: UUID
    login: str = Field(min_length=1, max_length=64)


class Session(DTO):
    user: User
    csrf_token: str = Field(min_length=32, repr=False)
    expires_at: UtcDateTime


class Login(DTO):
    login: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024, exclude=True, repr=False)


class Connection(DTO):
    connection_ref: ConnectionRef
    display_name: DisplayName


class NodeCreate(Connection):
    pass


class PartialDTO(DTO):
    @model_serializer(mode="wrap")
    def only_present_fields(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class NodePatch(PartialDTO):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})
    display_name: DisplayName = Field(default_factory=_omitted)
    enabled: StrictBool = Field(default_factory=_omitted)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one change is required")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class Node(DTO):
    id: UUID
    display_name: DisplayName
    enabled: StrictBool
    syncthing_device_id: NonEmpty | None
    api_status: ApiStatus
    last_checked_at: UtcDateTime | None
    last_seen_at: UtcDateTime | None
    is_stale: StrictBool
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def check_times(self) -> Self:
        if self.last_seen_at and self.last_checked_at and self.last_seen_at > self.last_checked_at:
            raise ValueError("Successful observation cannot be later than the last check")
        return self


class Folder(DTO):
    id: UUID
    node_id: UUID
    syncthing_folder_id: SyncthingFolderId
    label: DisplayName
    present: StrictBool
    actual_paused: StrictBool | None
    config_observed_at: UtcDateTime | None
    runtime_state: str | None
    runtime_observed_at: UtcDateTime | None
    need_bytes: NonNegative | None
    pull_errors: NonNegative | None
    desired_paused: StrictBool | None
    active_rule_id: UUID | None
    is_stale: StrictBool

    @model_validator(mode="after")
    def check_observation_pairs(self) -> Self:
        if (self.actual_paused is None) != (self.config_observed_at is None):
            raise ValueError("Configuration observation must have a value and time")
        if (self.runtime_state is None) != (self.runtime_observed_at is None):
            raise ValueError("Runtime observation must have a value and time")
        if (self.desired_paused is None) != (self.active_rule_id is None):
            raise ValueError("Desired state requires an active rule")
        return self


class RuleFields(DTO):
    timezone: IanaTimezone
    days_of_week: Days
    start_time: HHMM
    end_time: HHMM
    enabled: StrictBool

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if self.start_time == self.end_time:
            raise PydanticCustomError("invalid_window", "Window boundaries must differ")
        return self


class RuleCreate(RuleFields):
    folder_id: UUID
    timezone: IanaTimezone = "Europe/Moscow"
    enabled: StrictBool = True


class RulePatch(PartialDTO):
    model_config = ConfigDict(json_schema_extra={"minProperties": 2})
    expected_version: Positive
    timezone: IanaTimezone = Field(default_factory=_omitted)
    days_of_week: Days = Field(default_factory=_omitted)
    start_time: HHMM = Field(default_factory=_omitted)
    end_time: HHMM = Field(default_factory=_omitted)
    enabled: StrictBool = Field(default_factory=_omitted)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set - {"expected_version"}:
            raise ValueError("At least one change is required")
        if self.start_time is not None and self.start_time == self.end_time:
            raise PydanticCustomError("invalid_window", "Window boundaries must differ")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude={"expected_version"}, exclude_unset=True)

    def apply_to(self, current: RuleFields) -> RuleFields:
        # B1 checks expected_version under the shared folder lock, then validates
        # the complete merged value; model_copy(update=...) would skip validation.
        fields = {name: getattr(current, name) for name in RuleFields.model_fields}
        return RuleFields.model_validate(fields | self.changes())


class Rule(RuleFields):
    id: UUID
    folder_id: UUID
    version: Positive
    created_at: UtcDateTime
    updated_at: UtcDateTime


class RuleSnapshot(RuleFields):
    rule_id: UUID
    version: Positive
    folder_id: UUID


class Resume(DTO):
    folder_id: UUID
    idempotency_key: UUID


class Execution(DTO):
    id: UUID
    node_id: UUID
    folder_id: UUID
    source: Literal["rule", "manual"]
    rule_id: UUID | None
    rule_version: Positive | None
    rule_snapshot: RuleSnapshot | None
    desired_paused: StrictBool
    status: ExecutionStatus
    observed_paused: StrictBool | None
    restart_required: StrictBool | None
    source_session_id: SessionId | None
    created_at: UtcDateTime
    sent_at: UtcDateTime | None
    verified_at: UtcDateTime | None
    completed_at: UtcDateTime | None
    error_code: NonEmpty | None

    @model_validator(mode="after")
    def check_evidence(self) -> Self:
        if self.source == "manual":
            if any(v is not None for v in (self.rule_id, self.rule_version, self.rule_snapshot)):
                raise ValueError("Manual execution cannot contain rule fields")
            if self.desired_paused:
                raise ValueError("Manual execution must resume")
        else:
            snapshot = self.rule_snapshot
            if snapshot is None or self.rule_version != snapshot.version:
                raise ValueError("Rule execution requires a matching version and snapshot")
            if snapshot.folder_id != self.folder_id:
                raise ValueError("Snapshot must refer to the execution folder")
            if self.rule_id is not None and self.rule_id != snapshot.rule_id:
                raise ValueError("Snapshot must refer to the source rule")
        if self.status == ExecutionStatus.PENDING:
            if any(v is not None for v in (self.completed_at, self.verified_at, self.error_code)):
                raise ValueError("Pending execution cannot be completed")
        elif self.completed_at is None:
            raise ValueError("Terminal execution requires completion time")
        if self.status == ExecutionStatus.APPLIED:
            if (
                self.sent_at is None
                or self.verified_at is None
                or self.source_session_id is None
                or self.observed_paused != self.desired_paused
                or self.restart_required is not False
                or self.error_code is not None
            ):
                raise ValueError("Applied execution requires complete confirmation evidence")
        elif self.status != ExecutionStatus.PENDING and not self.error_code:
            raise ValueError("Unsuccessful execution requires a reason")
        if self.status == ExecutionStatus.CANCELLED and self.sent_at is not None:
            raise ValueError("A sent execution cannot be cancelled")
        if self.sent_at and self.sent_at < self.created_at:
            raise ValueError("Sending cannot precede creation")
        if self.completed_at and self.completed_at < (self.sent_at or self.created_at):
            raise ValueError("Completion cannot precede creation or sending")
        if self.verified_at and (
            self.sent_at is None
            or self.completed_at is None
            or not self.sent_at <= self.verified_at <= self.completed_at
        ):
            raise ValueError("Verification must be between sending and completion")
        return self


class EventPayload(PartialDTO):
    from_id: Positive = Field(default_factory=_omitted)
    to_id: Positive = Field(default_factory=_omitted)
    state: str = Field(default_factory=_omitted)
    previous_session_id: SessionId = Field(default_factory=_omitted)


class Event(DTO):
    id: UUID
    node_id: UUID
    folder_id: UUID | None
    kind: Literal["source", "gap", "restart"]
    source_session_id: SessionId
    source_event_id: Positive | None
    event_type: NonEmpty
    occurred_at: UtcDateTime | None
    received_at: UtcDateTime
    payload_summary: EventPayload

    @model_validator(mode="after")
    def check_kind(self) -> Self:
        if self.kind == "source":
            if self.source_event_id is None or self.occurred_at is None:
                raise ValueError("Source event requires a position and source time")
        elif self.source_event_id is not None:
            raise ValueError("Local marker cannot consume a source event position")
        if self.kind == "gap":
            payload = self.payload_summary
            if self.event_type != "HistoryGap" or payload.from_id is None or payload.to_id is None:
                raise ValueError("Gap requires a bounded range")
            if payload.from_id > payload.to_id:
                raise ValueError("Gap range must be ordered")
        if self.kind == "restart":
            if self.event_type not in {"SourceRestarted", "SourceContinuityLost"}:
                raise ValueError("Source boundary marker requires restart or continuity-loss type")
            if self.payload_summary.previous_session_id is None:
                raise ValueError("Source boundary marker requires the previous source epoch")
        return self


class Snapshot(DTO):
    id: UUID
    node_id: UUID
    folder_id: UUID | None
    scope: Literal["node", "folder"]
    observed_at: UtcDateTime
    success: StrictBool
    error_code: NonEmpty | None
    actual_paused: StrictBool | None
    runtime_state: str | None
    need_bytes: NonNegative | None
    pull_errors: NonNegative | None

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        measured = (self.actual_paused, self.runtime_state, self.need_bytes, self.pull_errors)
        if self.scope == "node" and (
            self.folder_id is not None or any(v is not None for v in measured)
        ):
            raise ValueError("Node probe cannot contain folder measurements")
        if self.scope == "folder" and self.folder_id is None:
            raise ValueError("Folder probe requires a folder")
        if self.success and self.error_code is not None:
            raise ValueError("Successful probe cannot contain an error")
        if not self.success and (not self.error_code or any(v is not None for v in measured)):
            raise ValueError("Failed probe requires an error and unknown measurements")
        return self


def _check_ratio(value: float | None, numerator: int, denominator: int) -> None:
    if not denominator:
        if value is not None:
            raise ValueError("An empty denominator requires null")
    elif value is None or not math.isclose(value, numerator / denominator, rel_tol=1e-12):
        raise ValueError("Ratio must match its numerator and denominator")


class CommandMetrics(DTO):
    applied: NonNegative
    failed: NonNegative
    unknown: NonNegative
    cancelled: NonNegative
    pending_created: NonNegative
    success_ratio: Ratio | None

    @model_validator(mode="after")
    def check_ratio(self) -> Self:
        _check_ratio(self.success_ratio, self.applied, self.applied + self.failed)
        return self


class ApiMetrics(DTO):
    successful_checks: NonNegative
    total_checks: NonNegative
    success_ratio: Ratio | None

    @model_validator(mode="after")
    def check_ratio(self) -> Self:
        if self.successful_checks > self.total_checks:
            raise ValueError("Successful checks cannot exceed all checks")
        _check_ratio(self.success_ratio, self.successful_checks, self.total_checks)
        return self


class AnalyticsDay(DTO):
    date: date
    commands: CommandMetrics
    api_checks: ApiMetrics


class AnalyticsPeriod(DTO):
    from_: UtcDateTime = Field(alias="from")
    to: UtcDateTime

    @model_validator(mode="after")
    def check_period(self) -> Self:
        if not timedelta(0) < self.to - self.from_ <= timedelta(days=31):
            raise PydanticCustomError(
                "invalid_period", "Period must be positive and at most 31 days"
            )
        return self


class Analytics(AnalyticsPeriod):
    node_id: UUID | None
    source: Literal["rule", "manual"] | None
    commands: CommandMetrics
    api_checks: ApiMetrics
    daily: tuple[AnalyticsDay, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def check_days(self) -> Self:
        first = self.from_.date()
        last = (self.to - timedelta(microseconds=1)).date()
        expected = tuple(first + timedelta(days=day) for day in range((last - first).days + 1))
        if tuple(point.date for point in self.daily) != expected:
            raise ValueError("Daily points must cover all touched UTC dates in order")
        return self


class Page[T](DTO):
    items: tuple[T, ...]
    total: NonNegative
    limit: Positive = Field(le=100)
    offset: NonNegative

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if len(self.items) > self.limit or len(self.items) > max(0, self.total - self.offset):
            raise ValueError("Page items exceed the requested bounds")
        return self


class ConnectionPage(Page[Connection]):
    pass


class NodePage(Page[Node]):
    pass


class FolderPage(Page[Folder]):
    pass


class RulePage(Page[Rule]):
    pass


class ExecutionPage(Page[Execution]):
    pass


class EventPage(Page[Event]):
    pass


class SnapshotPage(Page[Snapshot]):
    pass
