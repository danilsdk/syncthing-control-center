from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.collection.planning import MAX_ID, plan_batch
from app.contracts.dto import EventPayload
from app.contracts.interfaces import (
    AdapterError,
    EventBatch,
    SourceCheckpoint,
    SourceEvent,
    StreamAnchor,
)

NODE = uuid4()
RAW = "2026-10-25T12:00:00.123456789Z"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def source(identifier, **changes):
    return SourceEvent(
        id=identifier,
        global_id=10**12,
        type="StateChanged",
        time_raw=RAW,
        syncthing_folder_id="shared",
        payload_summary=EventPayload(state="idle"),
        **changes,
    )


def checkpoint(epoch="a", anchor=1, raw=RAW):
    return SourceCheckpoint(
        node_id=NODE,
        start_time_raw=raw,
        source_session_id=epoch * 64,
        anchor=StreamAnchor.from_event(source(anchor)),
    )


def packet(*positions, epoch="a", raw=RAW, anchor=None, events=None):
    values = tuple(source(p) for p in positions) if events is None else events
    tail = values[-1] if values else source(anchor or 1)
    cp = checkpoint(epoch, tail.id, raw).model_copy(
        update={"anchor": StreamAnchor.from_event(tail)}
    )
    return EventBatch(
        source_session_id=cp.source_session_id, events=values, source_checkpoint=cp, observed_at=NOW
    )


def test_contiguous_positions_advance_only_by_source_id():
    plan = plan_batch(NODE, checkpoint(), 0, packet(1, 2, 3))
    assert plan.cursor == 3 and not plan.reconcile
    assert [row.source_event_id for row in plan.rows] == [1, 2, 3]
    assert all(row.occurred_at == datetime.fromisoformat(RAW) for row in plan.rows)
    assert plan.checkpoint.anchor.time_raw == RAW


def test_bounded_prefix_and_internal_gaps():
    plan = plan_batch(NODE, checkpoint(anchor=2), 2, packet(4, 7))
    assert plan.cursor == 7 and plan.reconcile
    assert [row.payload for row in plan.rows if row.kind == "gap"] == [
        {"from_id": 3, "to_id": 3},
        {"from_id": 5, "to_id": 6},
    ]
    assert all(row.source_event_id is None for row in plan.rows if row.kind == "gap")


def test_replayed_positions_are_ignored_without_cursor_regression():
    plan = plan_batch(NODE, checkpoint(anchor=3), 3, packet(1, 2, 3))
    assert plan.cursor == 3 and plan.rows == ()


def test_empty_same_epoch_updates_proof_without_advancing_cursor():
    plan = plan_batch(NODE, checkpoint(anchor=3), 3, packet(anchor=10))
    assert plan.cursor == 3 and plan.checkpoint.anchor.id == 10 and plan.rows == ()


def test_empty_changed_epoch_defers_without_resetting_old_cursor():
    plan = plan_batch(NODE, checkpoint(anchor=30), 30, packet(epoch="b"))
    assert plan.deferred and plan.cursor == 30 and plan.rows == ()


@pytest.mark.parametrize(
    "raw,boundary", [(RAW, "SourceContinuityLost"), ("2026-10-25T13:00:00Z", "SourceRestarted")]
)
def test_epoch_boundary_has_no_invented_prefix_gap(raw, boundary):
    plan = plan_batch(NODE, checkpoint(anchor=80), 80, packet(100, 102, epoch="b", raw=raw))
    assert plan.rows[0].event_type == boundary
    assert plan.rows[0].payload == {"previous_session_id": "a" * 64}
    assert plan.rows[0].source_event_id is None
    assert [row.payload for row in plan.rows if row.kind == "gap"] == [
        {"from_id": 101, "to_id": 101}
    ]
    assert plan.cursor == 102


def test_initial_unanchored_node_begins_at_available_window():
    plan = plan_batch(NODE, None, 0, packet(20, 21))
    assert plan.cursor == 21 and all(row.kind == "source" for row in plan.rows)


def test_legacy_checkpoint_cannot_claim_continuity():
    legacy = checkpoint().model_copy(update={"anchor": None})
    with pytest.raises(AdapterError, match="invalid_response"):
        plan_batch(NODE, legacy, 1, packet(2))
    result = plan_batch(NODE, legacy, 1, packet(2, epoch="b"))
    assert result.rows[0].event_type == "SourceContinuityLost"


def test_source_payload_cannot_impersonate_marker_or_persist_paths():
    event = source(2).model_copy(
        update={
            "payload_summary": EventPayload(
                state="C:/private/secret", from_id=1, to_id=100, previous_session_id="b" * 64
            )
        }
    )
    plan = plan_batch(NODE, checkpoint(), 1, packet(events=(event,)))
    assert plan.rows[0].payload == {}


@pytest.mark.parametrize(
    "mutation",
    [
        "uuid",
        "epoch",
        "order",
        "anchor",
        "changed_raw",
        "regressed_anchor",
        "fingerprint",
        "event_name",
        "long_type",
        "nonpositive",
    ],
)
def test_inconsistent_batch_is_rejected_in_full(mutation):
    previous, batch = checkpoint(), packet(2, 3)
    if mutation == "uuid":
        cp = batch.source_checkpoint.model_copy(update={"node_id": uuid4()})
        batch = batch.model_copy(update={"source_checkpoint": cp})
    elif mutation == "epoch":
        batch = batch.model_copy(update={"source_session_id": "b" * 64})
    elif mutation == "order":
        batch = batch.model_copy(update={"events": tuple(reversed(batch.events))})
    elif mutation == "anchor":
        batch = batch.model_copy(update={"source_checkpoint": checkpoint(anchor=2)})
    elif mutation == "changed_raw":
        batch = packet(2, raw="2026-10-26T00:00:00Z")
    elif mutation == "regressed_anchor":
        previous, batch = checkpoint(anchor=4), packet(3)
    elif mutation == "fingerprint":
        cp = batch.source_checkpoint.model_copy(
            update={
                "anchor": batch.source_checkpoint.anchor.model_copy(
                    update={"fingerprint": "b" * 64}
                )
            }
        )
        batch = batch.model_copy(update={"source_checkpoint": cp})
    elif mutation in {"event_name", "long_type"}:
        event = source(2).model_copy(
            update={"type": "x" * 81 if mutation == "long_type" else "C:/private/path"}
        )
        batch = packet(events=(event,))
    else:
        batch = batch.model_copy(update={"events": (source(2).model_copy(update={"id": 0}),)})
    with pytest.raises(AdapterError) as error:
        plan_batch(NODE, previous, 1, batch)
    assert error.value.code == "invalid_response"


def test_bigint_boundary_is_not_confused_with_an_anchor_or_global_id():
    plan = plan_batch(NODE, checkpoint(anchor=MAX_ID - 1), MAX_ID - 1, packet(MAX_ID))
    assert plan.cursor == MAX_ID and len(plan.rows) == 1
