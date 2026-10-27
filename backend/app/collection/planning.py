"""Pure event planning: no SQL, transport calls or assumptions across unproven epochs."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.contracts.interfaces import AdapterError, EventBatch, SourceCheckpoint, StreamAnchor

MAX_ID = 2**63 - 1


@dataclass(frozen=True)
class PlannedEvent:
    kind: Literal["source", "gap", "restart"]
    event_type: str
    source_event_id: int | None = None
    occurred_at: datetime | None = None
    external_folder_id: str | None = None
    payload: dict | None = None


@dataclass(frozen=True)
class BatchPlan:
    checkpoint: SourceCheckpoint
    cursor: int
    rows: tuple[PlannedEvent, ...]
    deferred: bool = False

    @property
    def reconcile(self) -> bool:
        return any(row.kind != "source" for row in self.rows)


def plan_batch(
    node_id: UUID,
    previous: SourceCheckpoint | None,
    cursor: int,
    batch: EventBatch,
) -> BatchPlan:
    """Reject a bad batch in full; only accepted source positions can advance the cursor."""
    try:
        # Revalidate nested data too: model_copy/model_construct can bypass frozen DTO validators.
        batch = EventBatch.model_validate(batch.model_dump())
        if type(cursor) is not int or not 0 <= cursor <= MAX_ID:
            raise ValueError
        if previous is not None:
            previous = SourceCheckpoint.model_validate(previous.model_dump())
            if previous.node_id != node_id:
                raise ValueError
        elif cursor != 0:
            raise ValueError
        checkpoint = batch.source_checkpoint
        if checkpoint.node_id != node_id or checkpoint.anchor.id < max(
            (event.id for event in batch.events), default=0
        ):
            raise ValueError
        same = previous is not None and previous.source_session_id == batch.source_session_id
        if same:
            if previous.anchor is None or previous.start_time_raw != checkpoint.start_time_raw:
                raise ValueError
            if checkpoint.anchor.id < max(previous.anchor.id, cursor):
                raise ValueError
            if checkpoint.anchor.id == previous.anchor.id and checkpoint.anchor != previous.anchor:
                raise ValueError
        for event in batch.events:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", event.type):
                raise ValueError
            if (
                event.id == checkpoint.anchor.id
                and StreamAnchor.from_event(event) != checkpoint.anchor
            ):
                raise ValueError
            if same and event.id == previous.anchor.id:
                if StreamAnchor.from_event(event) != previous.anchor:
                    raise ValueError
        if not same and not batch.events:
            return BatchPlan(checkpoint, cursor, (), deferred=True)

        rows = []
        if previous is not None and not same:
            rows.append(
                PlannedEvent(
                    "restart",
                    "SourceRestarted"
                    if previous.start_time_raw != checkpoint.start_time_raw
                    else "SourceContinuityLost",
                    payload={"previous_session_id": previous.source_session_id},
                )
            )
        accepted = [event for event in batch.events if not same or event.id > cursor]
        expected = cursor + 1 if same else accepted[0].id
        last = cursor if same else 0
        for event in accepted:
            if event.id > expected:
                rows.append(
                    PlannedEvent(
                        "gap", "HistoryGap", payload={"from_id": expected, "to_id": event.id - 1}
                    )
                )
            # Source data cannot impersonate local gap/boundary payloads or expose arbitrary text.
            state = event.payload_summary.model_dump(exclude_unset=True).get("state")
            payload = (
                {"state": state}
                if event.type == "StateChanged"
                and isinstance(state, str)
                and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", state)
                else {}
            )
            rows.append(
                PlannedEvent(
                    "source",
                    event.type,
                    event.id,
                    datetime.fromisoformat(event.time_raw).astimezone(UTC),
                    event.syncthing_folder_id,
                    payload,
                )
            )
            last, expected = event.id, event.id + 1
        return BatchPlan(checkpoint, last, tuple(rows))
    except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
        raise AdapterError("invalid_response") from None
