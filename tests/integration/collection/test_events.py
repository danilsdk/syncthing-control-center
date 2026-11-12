from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from app.collection import EventCollector
from app.config import Settings
from app.db import Database
from app.models import Folder, ManagedNode, SyncEvent
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .conftest import RAW, checkpoint, packet, source

pytestmark = pytest.mark.postgres


def rows(realm):
    with realm.database.session() as session:
        return list(session.scalars(select(SyncEvent)))


def canonical(realm):
    with realm.database.session() as session:
        node = session.get(ManagedNode, realm.nodes[0])
        return (
            node.source_start_time_raw,
            node.source_session_id,
            node.source_stream_anchor,
            node.last_event_id,
        )


def set_cursor(realm, value, *, legacy=False):
    cp = checkpoint(realm.nodes[0], anchor=value or 1)
    with realm.database.session() as session:
        node = session.get(ManagedNode, realm.nodes[0])
        node.last_event_id = value
        node.source_stream_anchor = None if legacy else cp.anchor.model_dump(mode="json")
        session.commit()


def test_persisted_batch_replay_and_folder_identity(realm):
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "stored" and result.events_inserted == 3 and result.cursor == 3
    assert all(row.folder_id == realm.folders[0] for row in rows(realm))
    assert all(row.source_event_id <= 3 for row in rows(realm))
    again = realm.collector.collect(realm.nodes[0])
    assert again.events_inserted == 0 and again.markers_inserted == 0 and again.cursor == 3
    assert len(rows(realm)) == 3 and canonical(realm)[3] == 3
    assert len(realm.factories) == 2 and all(f.closed for f in realm.factories)
    assert realm.factories[-1].targets[0].checkpoint.anchor.id == 3


def test_source_unique_conflict_preserves_original_row(realm):
    identifier = uuid4()
    with realm.database.session() as session:
        session.add(
            SyncEvent(
                id=identifier,
                node_id=realm.nodes[0],
                folder_id=realm.folders[0],
                kind="source",
                event_type="Starting",
                source_event_id=1,
                source_session_id="a" * 64,
                occurred_at=realm.clock.now_utc(),
                payload_summary={},
            )
        )
        session.commit()
    result = realm.collector.collect(realm.nodes[0])
    assert result.events_inserted == 2 and len(rows(realm)) == 3
    original = next(row for row in rows(realm) if row.id == identifier)
    assert original.event_type == "Starting" and original.payload_summary == {}


def test_bounded_gaps_do_not_repeat(realm):
    realm.batch = packet(realm.nodes[0], 3, 6)
    first = realm.collector.collect(realm.nodes[0])
    assert first.reconcile and first.markers_inserted == 2
    assert sorted(
        (r.payload_summary["from_id"], r.payload_summary["to_id"])
        for r in rows(realm)
        if r.kind == "gap"
    ) == [(1, 2), (4, 5)]
    second = realm.collector.collect(realm.nodes[0])
    assert second.markers_inserted == 0 and len(rows(realm)) == 4


def test_same_external_folder_ids_are_local_to_node(realm):
    realm.collector.collect(realm.nodes[0])
    realm.batch = packet(realm.nodes[1], 1, 2, 3)
    realm.collector.collect(realm.nodes[1])
    assert len(rows(realm)) == 6
    assert all(
        row.folder_id == realm.folders[realm.nodes.index(row.node_id)] for row in rows(realm)
    )


def test_unknown_folder_is_not_guessed_or_created(realm):
    realm.batch = packet(realm.nodes[0], events=[source(2, folder="C:/private/file")])
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "stored"
    assert all(row.folder_id is None for row in rows(realm))
    assert "private" not in str([row.payload_summary for row in rows(realm)])
    with realm.database.session() as session:
        assert len(list(session.scalars(select(Folder)))) == 2


@pytest.mark.parametrize(
    "raw,event_type", [(RAW, "SourceContinuityLost"), ("2026-10-25T14:00:00Z", "SourceRestarted")]
)
def test_changed_epoch_rebases_once_and_preserves_boundary(realm, raw, event_type):
    set_cursor(realm, 80)
    realm.batch = packet(realm.nodes[0], 100, 102, epoch="b", raw=raw)
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "stored" and result.cursor == 102 and result.reconcile
    assert [c[1:] for c in realm.calls] == [(80, 0), (0, 0)] and realm.system_calls == 1
    marker = next(r for r in rows(realm) if r.kind == "restart")
    assert marker.event_type == event_type and marker.source_event_id is None
    assert marker.payload_summary == {"previous_session_id": "a" * 64}
    assert [r.payload_summary for r in rows(realm) if r.kind == "gap"] == [
        {"from_id": 101, "to_id": 101}
    ]
    realm.collector.collect(realm.nodes[0])
    assert len([r for r in rows(realm) if r.kind == "restart"]) == 1


def test_legacy_anchor_rebases_without_reusing_cursor(realm):
    set_cursor(realm, 4, legacy=True)
    realm.batch = packet(realm.nodes[0], 2, 3, epoch="b")
    assert realm.collector.collect(realm.nodes[0]).outcome == "stored"
    assert canonical(realm)[3] == 3
    assert next(r for r in rows(realm) if r.kind == "restart").event_type == "SourceContinuityLost"


def test_empty_changed_epoch_cannot_persist_old_cursor_under_new_session(realm):
    set_cursor(realm, 8)
    before = canonical(realm)
    realm.batch = packet(realm.nodes[0], epoch="b")
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "deferred" and canonical(realm) == before and rows(realm) == []


def test_empty_same_epoch_persists_proof_but_not_an_anchor_cursor(realm):
    set_cursor(realm, 3)
    realm.batch = packet(realm.nodes[0], anchor=8)
    assert realm.collector.collect(realm.nodes[0]).cursor == 3
    assert canonical(realm)[2]["id"] == 8 and rows(realm) == []


def test_database_failure_after_insert_rolls_back_everything(realm):
    before = canonical(realm)
    realm.batch = packet(realm.nodes[0], 3, 5, epoch="b")
    with realm.database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION scc_fail_event_commit() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'public-private-failure-canary'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER scc_fail_event_commit BEFORE UPDATE ON managed_nodes "
                "FOR EACH ROW EXECUTE FUNCTION scc_fail_event_commit()"
            )
        )
    try:
        result = realm.collector.collect(realm.nodes[0])
        assert result.outcome == "error" and result.error_code == "database_unavailable"
        assert rows(realm) == [] and canonical(realm) == before
        assert "canary" not in str(result) and realm.factories[0].closed
    finally:
        with realm.database.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER scc_fail_event_commit ON managed_nodes"))
            connection.execute(text("DROP FUNCTION scc_fail_event_commit()"))


def test_late_batch_cannot_replace_a_newer_committed_batch(realm):
    point = realm.store.load(realm.nodes[0])

    def newer():
        assert realm.store.save(point, packet(realm.nodes[0], 1, 2, 3, 4)).outcome == "stored"

    realm.on_read = newer
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "conflict" and canonical(realm)[3] == 4 and len(rows(realm)) == 4


def test_two_concurrent_collectors_commit_only_one_batch(realm):
    barrier = Barrier(2)
    realm.on_read = lambda: barrier.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(realm.collector.collect, [realm.nodes[0]] * 2))
    assert sorted(result.outcome for result in results) == ["conflict", "stored"]
    assert len(rows(realm)) == 3 and canonical(realm)[3] == 3
    assert len(realm.factories) == 2 and all(f.closed for f in realm.factories)


@pytest.mark.parametrize("change", ["disable", "identity", "alias", "key", "url", "remove"])
def test_metadata_or_server_target_change_discards_inflight_batch(realm, change):
    before = canonical(realm)

    def changed():
        if change in {"key", "url", "remove"}:
            if change == "remove":
                realm.settings.connections.pop("node-a")
            else:
                old = realm.settings.connections["node-a"]
                field = "base_url" if change == "url" else "api_key"
                value = "http://127.0.0.1:28384" if change == "url" else "rotated-public-canary"
                realm.settings.connections["node-a"] = old.model_copy(
                    update={field: type(old.api_key)(value)}
                )
        else:
            with realm.database.session() as session:
                row = session.get(ManagedNode, realm.nodes[0])
                setattr(
                    row,
                    {
                        "disable": "enabled",
                        "identity": "syncthing_device_id",
                        "alias": "connection_ref",
                    }[change],
                    {"disable": False, "identity": "replacement", "alias": "other-alias"}[change],
                )
                session.commit()

    realm.on_read = changed
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "conflict" and rows(realm) == [] and canonical(realm) == before


def test_unrelated_registry_observation_does_not_invalidate_checkpoint(realm):
    def observed():
        with realm.database.session() as session:
            node = session.get(ManagedNode, realm.nodes[0])
            node.display_name, node.api_status = "New name", "auth_error"
            session.commit()

    realm.on_read = observed
    assert realm.collector.collect(realm.nodes[0]).outcome == "stored"
    with realm.database.session() as session:
        node = session.get(ManagedNode, realm.nodes[0])
        assert node.display_name == "New name" and node.api_status == "auth_error"


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "unreachable",
        "auth_error",
        "invalid_response",
        "identity_mismatch",
        "not_found",
        "session_changed",
    ],
)
def test_adapter_failure_leaves_no_partial_rows_and_closes_factory(realm, failure):
    before = canonical(realm)
    realm.failure = failure
    result = realm.collector.collect(realm.nodes[0])
    assert result.outcome == "error" and result.error_code == failure
    assert rows(realm) == [] and canonical(realm) == before and realm.factories[0].closed
    assert len(realm.calls) == (2 if failure == "session_changed" else 1)


def test_identity_during_explicit_rebase_is_checked(realm):
    set_cursor(realm, 9)
    realm.batch = packet(realm.nodes[0], 1, 2, epoch="b")
    realm.system_device = "other-device"
    result = realm.collector.collect(realm.nodes[0])
    assert result.error_code == "identity_mismatch" and len(realm.calls) == 1 and rows(realm) == []


@pytest.mark.parametrize("disabled", [True, False])
def test_disabled_or_missing_node_does_not_create_a_factory(realm, disabled):
    if disabled:
        with realm.database.session() as session:
            session.get(ManagedNode, realm.nodes[0]).enabled = False
            session.commit()
    result = realm.collector.collect(realm.nodes[0] if disabled else uuid4())
    assert result.outcome == "skipped" and realm.factories == [] and realm.calls == []


def test_invalid_batch_is_atomic(realm):
    realm.batch = realm.batch.model_copy(
        update={
            "source_checkpoint": realm.batch.source_checkpoint.model_copy(
                update={"node_id": uuid4()}
            )
        }
    )
    assert realm.collector.collect(realm.nodes[0]).error_code == "invalid_response"
    assert rows(realm) == [] and canonical(realm)[3] == 0


def test_unknown_commit_outcome_is_recovered_from_db_without_duplicates(realm):
    def unknown(_):
        raise OperationalError(
            "public-private-commit-canary", None, Exception("public-private-canary")
        )

    event.listen(Session, "after_commit", unknown, once=True)
    try:
        first = realm.collector.collect(realm.nodes[0])
    finally:
        event.remove(Session, "after_commit", unknown)
    assert first.error_code == "database_unavailable" and len(rows(realm)) == 3
    second = realm.collector.collect(realm.nodes[0])
    assert second.outcome == "stored" and second.events_inserted == 0 and len(rows(realm)) == 3
    assert realm.calls[-1][1] == 3 and all(f.closed for f in realm.factories)


def test_close_failure_does_not_hide_a_completed_commit(realm):
    realm.close_failure = True
    first = realm.collector.collect(realm.nodes[0])
    assert first.error_code == "resource_close_failed" and first.events_inserted == 3
    assert canonical(realm)[3] == 3 and len(rows(realm)) == 3
    realm.close_failure = False
    assert realm.collector.collect(realm.nodes[0]).events_inserted == 0


def test_database_unavailable_does_not_expose_private_configuration():
    settings = Settings(database_url=None)
    database = Database(settings)
    try:
        from .conftest import Clock

        result = EventCollector(settings, database, Clock()).collect(uuid4())
        assert result.error_code == "database_unavailable"
    finally:
        database.close()


def test_canonical_anchor_change_alone_invalidates_inflight_batch(realm):
    def changed():
        with realm.database.session() as session:
            session.get(ManagedNode, realm.nodes[0]).source_stream_anchor = checkpoint(
                realm.nodes[0], anchor=6
            ).anchor.model_dump(mode="json")
            session.commit()

    realm.on_read = changed
    assert realm.collector.collect(realm.nodes[0]).outcome == "conflict"
    assert rows(realm) == [] and canonical(realm)[2]["id"] == 6 and canonical(realm)[3] == 0


def test_initial_node_without_checkpoint_persists_first_available_window(realm):
    with realm.database.session() as session:
        node = session.get(ManagedNode, realm.nodes[0])
        node.source_start_time_raw = node.source_session_id = node.source_stream_anchor = None
        session.commit()
    realm.batch = packet(realm.nodes[0], 20, 21)
    result = realm.collector.collect(realm.nodes[0])
    assert result.cursor == 21 and result.markers_inserted == 0 and result.events_inserted == 2
    assert canonical(realm)[2]["id"] == 21


def test_changed_epoch_during_rebase_is_not_persisted(realm):
    set_cursor(realm, 8)
    before = canonical(realm)
    realm.batch = packet(realm.nodes[0], 1, 2, epoch="b")

    def changed():
        if len(realm.calls) == 2:
            realm.batch = packet(realm.nodes[0], 1, 2, epoch="c")

    realm.on_read = changed
    result = realm.collector.collect(realm.nodes[0])
    assert result.error_code == "session_changed" and len(realm.calls) == 2
    assert rows(realm) == [] and canonical(realm) == before


def test_repeated_old_readpoint_cannot_duplicate_boundary(realm):
    before = realm.store.load(realm.nodes[0])
    batch = packet(realm.nodes[0], 100, 101, epoch="b")
    assert realm.store.save(before, batch).outcome == "stored"
    assert realm.store.save(before, batch).outcome == "conflict"
    assert len(rows(realm)) == 3 and len([r for r in rows(realm) if r.kind == "restart"]) == 1


def test_payload_summary_is_rebuilt_before_sql(realm):
    from app.contracts.dto import EventPayload

    item = source(2).model_copy(
        update={
            "payload_summary": EventPayload(
                state="C:/private/secret", from_id=1, to_id=99, previous_session_id="b" * 64
            )
        }
    )
    realm.batch = packet(realm.nodes[0], events=[item])
    assert realm.collector.collect(realm.nodes[0]).outcome == "stored"
    assert all(r.payload_summary == {} for r in rows(realm) if r.kind == "source")
    assert "private" not in str([r.payload_summary for r in rows(realm)])


def test_unexpected_source_error_is_safe_and_closes_owned_factory(realm):
    def failure():
        raise RuntimeError("public-private-exception-canary")

    realm.on_read = failure
    result = realm.collector.collect(realm.nodes[0])
    assert result.error_code == "internal_error" and "canary" not in str(result)
    assert rows(realm) == [] and realm.factories[0].closed


def test_new_collector_instance_resumes_persisted_checkpoint(realm):
    realm.collector.collect(realm.nodes[0])
    new = EventCollector(
        realm.settings,
        realm.database,
        realm.clock,
        factory_provider=realm.collector.factory_provider,
    )
    realm.batch = packet(realm.nodes[0], 4, 5)
    assert new.collect(realm.nodes[0]).cursor == 5
    assert realm.calls[-1][1] == 3 and realm.factories[-1].targets[0].checkpoint.anchor.id == 3
    assert len(rows(realm)) == 5
