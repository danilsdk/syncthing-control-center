"""A0 data model. Cross-row scheduling and state transitions belong to services."""

from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdentityMixin, TimestampsMixin


class AppUser(IdentityMixin, TimestampsMixin, Base):
    __tablename__ = "app_users"
    __table_args__ = (
        UniqueConstraint("login"),
        CheckConstraint("login ~ '^[a-z0-9_.-]{1,64}$'", name="normalized_login"),
        CheckConstraint("btrim(password_hash) <> ''", name="password_hash_nonempty"),
    )

    login: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedNode(IdentityMixin, TimestampsMixin, Base):
    __tablename__ = "managed_nodes"
    __table_args__ = (
        UniqueConstraint("connection_ref"),
        CheckConstraint("btrim(display_name) <> ''", name="display_name_nonempty"),
        CheckConstraint("connection_ref ~ '^[a-z][a-z0-9_-]{0,63}$'", name="connection_alias"),
        CheckConstraint(
            "api_status IN ('unknown','ok','unreachable','auth_error',"
            "'invalid_response','identity_mismatch')",
            name="api_status",
        ),
        CheckConstraint("last_event_id >= 0", name="cursor_nonnegative"),
        CheckConstraint(
            "(source_start_time_raw IS NULL AND source_session_id IS NULL AND last_event_id = 0) "
            "OR (source_start_time_raw IS NOT NULL AND btrim(source_start_time_raw) <> '' "
            "AND source_session_id IS NOT NULL)",
            name="source_session_pair",
        ),
        CheckConstraint("source_session_id ~ '^[0-9a-f]{64}$'", name="source_session_hash"),
        CheckConstraint(
            "source_stream_anchor IS NULL OR (source_session_id IS NOT NULL AND "
            "jsonb_typeof(source_stream_anchor) = 'object' AND "
            "source_stream_anchor ?& ARRAY['id','time_raw','fingerprint'] AND "
            "source_stream_anchor - ARRAY['id','time_raw','fingerprint'] = '{}'::jsonb AND "
            "jsonb_typeof(source_stream_anchor->'id') = 'number' AND "
            "CASE WHEN source_stream_anchor->>'id' ~ '^[1-9][0-9]{0,18}$' "
            "THEN (source_stream_anchor->>'id')::numeric <= 9223372036854775807 "
            "ELSE false END AND "
            "jsonb_typeof(source_stream_anchor->'time_raw') = 'string' AND "
            "source_stream_anchor->>'time_raw' ~ "
            "'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            "([.][0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$' AND "
            "jsonb_typeof(source_stream_anchor->'fingerprint') = 'string' AND "
            "source_stream_anchor->>'fingerprint' ~ '^[0-9a-f]{64}$')",
            name="stream_anchor",
        ),
        CheckConstraint("last_seen_at <= last_checked_at", name="seen_before_check"),
    )

    display_name: Mapped[str] = mapped_column(String(80))
    connection_ref: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    syncthing_device_id: Mapped[str | None] = mapped_column(Text)
    api_status: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_start_time_raw: Mapped[str | None] = mapped_column(Text)
    source_session_id: Mapped[str | None] = mapped_column(CHAR(64))
    source_stream_anchor: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    last_event_id: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class Folder(IdentityMixin, TimestampsMixin, Base):
    __tablename__ = "folders"
    __table_args__ = (
        UniqueConstraint("node_id", "syncthing_folder_id", name="uq_folders_node_external_id"),
        UniqueConstraint("id", "node_id", name="uq_folders_id_node_id"),
        CheckConstraint("btrim(syncthing_folder_id) <> ''", name="external_id_nonempty"),
        CheckConstraint("btrim(label) <> ''", name="label_nonempty"),
        CheckConstraint(
            "(actual_paused IS NULL) = (config_observed_at IS NULL)", name="config_pair"
        ),
        CheckConstraint(
            "(runtime_state IS NULL) = (runtime_observed_at IS NULL)", name="runtime_pair"
        ),
        CheckConstraint("need_bytes >= 0 AND pull_errors >= 0", name="counters_nonnegative"),
    )

    node_id: Mapped[UUID] = mapped_column(ForeignKey("managed_nodes.id", ondelete="RESTRICT"))
    syncthing_folder_id: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(80))
    present: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    actual_paused: Mapped[bool | None] = mapped_column(Boolean)
    config_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_state: Mapped[str | None] = mapped_column(Text)
    runtime_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    need_bytes: Mapped[int | None] = mapped_column(BigInteger)
    pull_errors: Mapped[int | None] = mapped_column(BigInteger)


class SyncRule(IdentityMixin, TimestampsMixin, Base):
    __tablename__ = "sync_rules"
    __table_args__ = (
        CheckConstraint("days_mask BETWEEN 1 AND 127", name="days_mask"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(timezone) <> ''", name="timezone_nonempty"),
        CheckConstraint("start_time <> end_time", name="window_nonempty"),
        CheckConstraint(
            "EXTRACT(SECOND FROM start_time) = 0 AND EXTRACT(SECOND FROM end_time) = 0 "
            "AND start_time < TIME '24:00' AND end_time < TIME '24:00'",
            name="minute_precision",
        ),
        Index(
            "uq_sync_rules_active_folder",
            "folder_id",
            unique=True,
            postgresql_where=text("enabled IS TRUE"),
        ),
    )

    folder_id: Mapped[UUID] = mapped_column(ForeignKey("folders.id", ondelete="RESTRICT"))
    timezone: Mapped[str] = mapped_column(String(64), server_default=text("'Europe/Moscow'"))
    days_mask: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time(timezone=False))
    end_time: Mapped[time] = mapped_column(Time(timezone=False))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CommandExecution(IdentityMixin, Base):
    __tablename__ = "command_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["folder_id", "node_id"], ["folders.id", "folders.node_id"], ondelete="RESTRICT"
        ),
        CheckConstraint("source IN ('rule','manual')", name="source"),
        CheckConstraint("rule_version >= 1", name="rule_version_positive"),
        CheckConstraint(
            "(source = 'rule' AND rule_version IS NOT NULL AND rule_snapshot IS NOT NULL "
            "AND jsonb_typeof(rule_snapshot) = 'object' "
            "AND requested_by IS NULL AND idempotency_key IS NULL) OR "
            "(source = 'manual' AND rule_id IS NULL AND rule_version IS NULL "
            "AND rule_snapshot IS NULL AND requested_by IS NOT NULL "
            "AND idempotency_key IS NOT NULL AND desired_paused IS FALSE)",
            name="source_fields",
        ),
        CheckConstraint(
            "status IN ('pending','applied','failed','unknown','cancelled')", name="status"
        ),
        CheckConstraint("source_session_id ~ '^[0-9a-f]{64}$'", name="source_session_hash"),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL AND verified_at IS NULL "
            "AND error_code IS NULL) OR (status <> 'pending' AND completed_at IS NOT NULL)",
            name="completion",
        ),
        CheckConstraint(
            "status <> 'applied' OR (sent_at IS NOT NULL AND verified_at IS NOT NULL "
            "AND observed_paused IS NOT NULL AND observed_paused = desired_paused "
            "AND restart_required IS FALSE AND source_session_id IS NOT NULL "
            "AND error_code IS NULL)",
            name="applied_evidence",
        ),
        CheckConstraint(
            "status NOT IN ('failed','unknown','cancelled') OR "
            "(error_code IS NOT NULL AND btrim(error_code) <> '')",
            name="failure_reason",
        ),
        CheckConstraint("status <> 'cancelled' OR sent_at IS NULL", name="cancelled_unsent"),
        CheckConstraint(
            "(sent_at IS NULL OR sent_at >= created_at) AND "
            "(completed_at IS NULL OR completed_at >= created_at) AND "
            "(sent_at IS NULL OR completed_at IS NULL OR sent_at <= completed_at) AND "
            "(verified_at IS NULL OR (sent_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND verified_at >= sent_at AND verified_at <= completed_at))",
            name="time_order",
        ),
        Index(
            "uq_command_executions_pending_folder",
            "folder_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_command_executions_manual_key",
            "requested_by",
            "idempotency_key",
            unique=True,
            postgresql_where=text("source = 'manual'"),
        ),
        Index("ix_command_executions_node_completed", "node_id", "completed_at"),
        Index("ix_command_executions_folder_created", "folder_id", "created_at"),
        Index("ix_command_executions_rule_created", "rule_id", "created_at"),
    )

    node_id: Mapped[UUID] = mapped_column()
    folder_id: Mapped[UUID] = mapped_column()
    source: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[UUID | None] = mapped_column(ForeignKey("sync_rules.id", ondelete="SET NULL"))
    rule_version: Mapped[int | None] = mapped_column(Integer)
    rule_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[UUID | None] = mapped_column()
    desired_paused: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    observed_paused: Mapped[bool | None] = mapped_column(Boolean)
    restart_required: Mapped[bool | None] = mapped_column(Boolean)
    source_session_id: Mapped[str | None] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class SyncEvent(IdentityMixin, Base):
    __tablename__ = "sync_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["folder_id", "node_id"], ["folders.id", "folders.node_id"], ondelete="RESTRICT"
        ),
        CheckConstraint("kind IN ('source','gap','restart')", name="kind"),
        CheckConstraint("source_session_id ~ '^[0-9a-f]{64}$'", name="source_session_hash"),
        CheckConstraint(
            "(kind = 'source' AND source_event_id IS NOT NULL AND source_event_id >= 1 "
            "AND occurred_at IS NOT NULL) OR (kind <> 'source' AND source_event_id IS NULL)",
            name="source_fields",
        ),
        CheckConstraint("btrim(event_type) <> ''", name="event_type_nonempty"),
        CheckConstraint(
            "(kind <> 'gap' OR event_type = 'HistoryGap') AND "
            "(kind <> 'restart' OR event_type IN ('SourceRestarted','SourceContinuityLost'))",
            name="marker_type",
        ),
        CheckConstraint(
            "jsonb_typeof(payload_summary) = 'object' AND "
            "payload_summary - "
            "ARRAY['from_id','to_id','state','previous_session_id'] = '{}'::jsonb",
            name="payload_allowlist",
        ),
        Index(
            "uq_sync_events_source_position",
            "node_id",
            "source_session_id",
            "source_event_id",
            unique=True,
            postgresql_where=text("kind = 'source'"),
        ),
        Index("ix_sync_events_node_received", "node_id", "received_at"),
        Index("ix_sync_events_folder_received", "folder_id", "received_at"),
    )

    node_id: Mapped[UUID] = mapped_column(ForeignKey("managed_nodes.id", ondelete="RESTRICT"))
    folder_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(Text)
    source_session_id: Mapped[str] = mapped_column(CHAR(64))
    source_event_id: Mapped[int | None] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), server_default=text("'{}'::jsonb")
    )


class StatusSnapshot(IdentityMixin, Base):
    __tablename__ = "status_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["folder_id", "node_id"], ["folders.id", "folders.node_id"], ondelete="RESTRICT"
        ),
        CheckConstraint("scope IN ('node','folder')", name="scope"),
        CheckConstraint(
            "(scope = 'node' AND folder_id IS NULL AND actual_paused IS NULL "
            "AND runtime_state IS NULL AND need_bytes IS NULL AND pull_errors IS NULL) OR "
            "(scope = 'folder' AND folder_id IS NOT NULL)",
            name="scope_fields",
        ),
        CheckConstraint(
            "(success IS TRUE AND error_code IS NULL) OR "
            "(success IS FALSE AND error_code IS NOT NULL AND btrim(error_code) <> '' "
            "AND actual_paused IS NULL AND runtime_state IS NULL "
            "AND need_bytes IS NULL AND pull_errors IS NULL)",
            name="result_fields",
        ),
        CheckConstraint("need_bytes >= 0 AND pull_errors >= 0", name="counters_nonnegative"),
        Index("ix_status_snapshots_node_scope_observed", "node_id", "scope", "observed_at"),
        Index("ix_status_snapshots_folder_observed", "folder_id", "observed_at"),
    )

    node_id: Mapped[UUID] = mapped_column(ForeignKey("managed_nodes.id", ondelete="RESTRICT"))
    folder_id: Mapped[UUID | None] = mapped_column()
    scope: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    success: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(80))
    actual_paused: Mapped[bool | None] = mapped_column(Boolean)
    runtime_state: Mapped[str | None] = mapped_column(Text)
    need_bytes: Mapped[int | None] = mapped_column(BigInteger)
    pull_errors: Mapped[int | None] = mapped_column(BigInteger)
