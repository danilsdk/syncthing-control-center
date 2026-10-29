"""D-01 proof migration, preservation of old history, and checkpoint atomicity."""

from uuid import uuid4

import pytest
from app.contracts.dto import EventPayload
from app.contracts.interfaces import SourceCheckpoint, SourceEvent, StreamAnchor
from app.models import ManagedNode, SyncEvent
from sqlalchemy import MetaData, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from .conftest import migrate

pytestmark = pytest.mark.postgres
RAW = "2026-10-25T00:00:00.123456789+03:00"
ANCHOR = StreamAnchor.from_event(
    SourceEvent(
        id=3,
        global_id=7,
        time_raw=RAW,
        type="StateChanged",
        syncthing_folder_id="demo",
        payload_summary=EventPayload(state="idle"),
    )
).model_dump(mode="json")


def node_values(node_id, **extra):
    return (
        dict(
            id=node_id,
            display_name="Demo",
            connection_ref="d01-test",
            source_start_time_raw=RAW,
            source_session_id="a" * 64,
            last_event_id=3,
        )
        | extra
    )


def test_upgrade_of_populated_legacy_state_preserves_cursor_session_and_history(pg):
    node_id, event_id = uuid4(), uuid4()
    with pg.begin() as connection:
        migrate(connection, "downgrade", "a003_initial_schema")
        connection.execute(
            text(
                "INSERT INTO managed_nodes(id, display_name, connection_ref, "
                "source_start_time_raw, source_session_id,last_event_id) "
                "VALUES(:id,'Demo','d01-test',:raw,:session,3)"
            ),
            {"id": node_id, "raw": RAW, "session": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO sync_events(id,node_id,kind,source_session_id,source_event_id,"
                "event_type,occurred_at) VALUES(:id,:node,'source',:session,3,'StateChanged',now())"
            ),
            {"id": event_id, "node": node_id, "session": "a" * 64},
        )
        migrate(connection)
        node = (
            connection.execute(select(ManagedNode).where(ManagedNode.id == node_id))
            .mappings()
            .one()
        )
        assert node["last_event_id"] == 3 and node["source_session_id"] == "a" * 64
        assert node["source_stream_anchor"] is None
        assert connection.scalar(select(SyncEvent.id).where(SyncEvent.id == event_id)) == event_id
        migrate(connection, "downgrade", "a003_initial_schema")
        assert "source_stream_anchor" not in {
            col["name"] for col in inspect(connection).get_columns("managed_nodes")
        }
        migrate(connection)


def test_checkpoint_roundtrip_preserves_nanoseconds_and_downgrade_refuses_to_discard_it(pg):
    node_id = uuid4()
    with pg.begin() as connection:
        connection.execute(
            insert(ManagedNode).values(**node_values(node_id, source_stream_anchor=ANCHOR))
        )
        stored = connection.scalar(select(ManagedNode.source_stream_anchor))
    checkpoint = SourceCheckpoint(
        node_id=node_id, start_time_raw=RAW, source_session_id="a" * 64, anchor=stored
    )
    assert checkpoint.anchor.time_raw == RAW
    with pytest.raises(RuntimeError, match="explicit recovery plan"), pg.begin() as connection:
        migrate(connection, "downgrade", "a003_initial_schema")
    with pg.begin() as connection:
        assert connection.scalar(select(ManagedNode.source_stream_anchor)) == ANCHOR


@pytest.mark.parametrize(
    "anchor",
    [
        {},
        {**ANCHOR, "id": False},
        {**ANCHOR, "id": 0},
        {**ANCHOR, "fingerprint": "invalid"},
        {**ANCHOR, "path": "private"},
        {**ANCHOR, "time_raw": None},
        {**ANCHOR, "fingerprint": None},
        {**ANCHOR, "time_raw": "invalid"},
    ],
)
def test_invalid_checkpoint_is_rejected_by_the_database(pg, anchor):
    with pytest.raises(IntegrityError) as caught, pg.begin() as connection:
        connection.execute(
            insert(ManagedNode).values(**node_values(uuid4(), source_stream_anchor=anchor))
        )
    assert caught.value.orig.diag.constraint_name == "ck_managed_nodes_stream_anchor"


@pytest.fixture(params=["migration", "model"])
def anchor_table(pg, request):
    if request.param == "migration":
        yield ManagedNode.__table__
        return
    schema = "anchor_boundary_" + uuid4().hex
    table = ManagedNode.__table__.to_metadata(MetaData(), schema=schema)
    with pg.begin() as connection:
        connection.execute(CreateSchema(schema))
        table.create(connection)
    try:
        yield table
    finally:
        with pg.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


@pytest.mark.parametrize("anchor_id", [1, 2**63 - 1])
def test_anchor_id_boundaries_roundtrip_through_sql_and_dto(pg, anchor_table, anchor_id):
    node_id = uuid4()
    with pg.begin() as connection:
        connection.execute(
            insert(anchor_table).values(
                **node_values(node_id, source_stream_anchor={**ANCHOR, "id": anchor_id})
            )
        )
        stored = connection.scalar(select(anchor_table.c.source_stream_anchor))
    checkpoint = SourceCheckpoint(
        node_id=node_id, start_time_raw=RAW, source_session_id="a" * 64, anchor=stored
    )
    assert checkpoint.anchor.id == anchor_id


@pytest.mark.parametrize("anchor_id", [2**63, 10**19 - 1, 10**19, 1.5, "invalid"])
def test_invalid_anchor_id_is_rejected_by_sql(pg, anchor_table, anchor_id):
    with pytest.raises(IntegrityError) as caught, pg.begin() as connection:
        connection.execute(
            insert(anchor_table).values(
                **node_values(uuid4(), source_stream_anchor={**ANCHOR, "id": anchor_id})
            )
        )
    assert caught.value.orig.diag.constraint_name == "ck_managed_nodes_stream_anchor"


def test_checkpoint_and_cursor_rollback_together_when_event_batch_cannot_commit(pg):
    node_id = uuid4()
    with pg.begin() as connection:
        connection.execute(
            insert(ManagedNode).values(**node_values(node_id, source_stream_anchor=ANCHOR))
        )
    with pytest.raises(IntegrityError), pg.begin() as connection:
        connection.execute(
            update(ManagedNode)
            .where(ManagedNode.id == node_id)
            .values(
                source_session_id="b" * 64,
                last_event_id=1,
                source_stream_anchor={**ANCHOR, "id": 1},
            )
        )
        for _ in range(2):
            connection.execute(
                insert(SyncEvent).values(
                    id=uuid4(),
                    node_id=node_id,
                    kind="source",
                    source_session_id="b" * 64,
                    source_event_id=1,
                    event_type="StateChanged",
                    occurred_at=text("now()"),
                )
            )
    with pg.begin() as connection:
        node = (
            connection.execute(select(ManagedNode).where(ManagedNode.id == node_id))
            .mappings()
            .one()
        )
        assert node["source_session_id"] == "a" * 64 and node["last_event_id"] == 3
        assert node["source_stream_anchor"] == ANCHOR
        assert connection.scalar(select(SyncEvent.id).limit(1)) is None


def test_continuity_loss_marker_is_distinct_from_a_proven_restart(pg):
    node_id, event_id = uuid4(), uuid4()
    with pg.begin() as connection:
        connection.execute(insert(ManagedNode).values(**node_values(node_id)))
        connection.execute(
            insert(SyncEvent).values(
                id=event_id,
                node_id=node_id,
                kind="restart",
                source_session_id="b" * 64,
                event_type="SourceContinuityLost",
                source_event_id=None,
                occurred_at=None,
                payload_summary={"previous_session_id": "a" * 64},
            )
        )
        assert (
            connection.scalar(select(SyncEvent.event_type).where(SyncEvent.id == event_id))
            == "SourceContinuityLost"
        )
    with pytest.raises(RuntimeError, match="explicit recovery plan"), pg.begin() as connection:
        migrate(connection, "downgrade", "a003_initial_schema")
