"""Persist D-01 v2 continuity proof; preserve legacy events and cursors.

Revision ID: a101_stream_anchor
Revises: a003_initial_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a101_stream_anchor"
down_revision = "a003_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "managed_nodes", sa.Column("source_stream_anchor", postgresql.JSONB(none_as_null=True))
    )
    op.create_check_constraint(
        "stream_anchor",
        "managed_nodes",
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
    )
    op.drop_constraint(op.f("ck_sync_events_marker_type"), "sync_events", type_="check")
    op.create_check_constraint(
        "marker_type",
        "sync_events",
        "(kind <> 'gap' OR event_type = 'HistoryGap') AND "
        "(kind <> 'restart' OR event_type IN ('SourceRestarted','SourceContinuityLost'))",
    )


def downgrade():
    # Do not discard proof or relabel an unknown boundary as a proven restart.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM managed_nodes WHERE source_stream_anchor IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM sync_events WHERE event_type = 'SourceContinuityLost')"
        )
    ):
        raise RuntimeError(
            "D-01 continuity data exists; downgrade requires an explicit recovery plan"
        )
    op.drop_constraint(op.f("ck_sync_events_marker_type"), "sync_events", type_="check")
    op.create_check_constraint(
        "marker_type",
        "sync_events",
        "(kind <> 'gap' OR event_type = 'HistoryGap') AND "
        "(kind <> 'restart' OR event_type = 'SourceRestarted')",
    )
    op.drop_constraint(op.f("ck_managed_nodes_stream_anchor"), "managed_nodes", type_="check")
    op.drop_column("managed_nodes", "source_stream_anchor")
