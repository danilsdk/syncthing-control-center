from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.collection import CollectionService, ObservationCollector
from app.models import Folder, ManagedNode, StatusSnapshot
from sqlalchemy import select, text

from .conftest import packet

pytestmark = pytest.mark.postgres


def collector(realm):
    realm.clock.current = realm.clock.current or datetime.now(UTC)
    return ObservationCollector(
        realm.settings,
        realm.database,
        realm.clock,
        factory_provider=realm.collector.factory_provider,
    )


def snapshots(realm):
    with realm.database.session() as session:
        return list(session.scalars(select(StatusSnapshot)))


def folder(realm):
    with realm.database.session() as session:
        return session.get(Folder, realm.folders[0])


def proof(realm):
    with realm.database.session() as session:
        node = session.get(ManagedNode, realm.nodes[0])
        return node.source_session_id, node.source_stream_anchor, node.last_event_id


def test_successful_probe_has_one_node_and_distinct_config_runtime_rows(realm):
    before = proof(realm)
    result = collector(realm).observe(realm.nodes[0])
    assert result.outcome == "stored" and result.snapshots_inserted == 5 and result.cache_updated
    rows = snapshots(realm)
    assert len([r for r in rows if r.scope == "node"]) == 1
    assert len([r for r in rows if r.actual_paused is False]) == 2
    assert len([r for r in rows if r.runtime_state == "idle"]) == 2
    assert all(r.success for r in rows) and proof(realm) == before
    assert folder(realm).actual_paused is False and folder(realm).need_bytes == 7
    assert all(f.closed for f in realm.factories)


@pytest.mark.parametrize(
    "error",
    [
        "timeout",
        "unreachable",
        "auth_error",
        "invalid_response",
        "identity_mismatch",
        "session_changed",
        "not_found",
    ],
)
def test_actual_failed_node_probe_is_recorded_once_without_fabricated_folders(realm, error):
    realm.system_failure = error
    before = proof(realm)
    result = collector(realm).observe(realm.nodes[0])
    assert result.node_error == error and result.snapshots_inserted == 1
    row = snapshots(realm)[0]
    assert row.scope == "node" and not row.success and row.error_code == error
    assert row.actual_paused is row.runtime_state is row.need_bytes is None
    assert realm.config_calls == realm.runtime_calls == 0
    assert proof(realm) == before and folder(realm).actual_paused is None


def test_runtime_failure_preserves_prior_cache_and_successful_paused_config(realm):
    service = collector(realm)
    service.observe(realm.nodes[0])
    original = folder(realm)
    realm.clock.current += timedelta(seconds=31)
    realm.configs[0] = ("shared", "Shared", True)
    realm.runtime_failures["shared"] = "invalid_response"
    result = service.observe(realm.nodes[0])
    current = folder(realm)
    assert result.runtime_failures == 1 and result.node_error is None
    assert current.actual_paused is True and current.runtime_state == "idle"
    assert current.runtime_observed_at == original.runtime_observed_at
    failed = [r for r in snapshots(realm) if not r.success]
    assert len(failed) == 1 and failed[0].error_code == "runtime_invalid_response"
    assert failed[0].runtime_state is failed[0].actual_paused is failed[0].need_bytes is None
    assert len([r for r in snapshots(realm) if r.scope == "node" and r.success]) == 2


def test_config_failure_does_not_remove_folders_or_invent_measurements(realm):
    realm.config_failure = "timeout"
    result = collector(realm).observe(realm.nodes[0])
    assert result.config_error == "timeout" and result.snapshots_inserted == 1
    assert snapshots(realm)[0].success and folder(realm).present is False
    assert realm.runtime_calls == 0


def test_epoch_change_during_config_discards_mixed_folder_measurements(realm):
    realm.on_config = lambda: setattr(realm, "batch", packet(realm.nodes[0], 1, 2, epoch="b"))
    before = proof(realm)
    result = collector(realm).observe(realm.nodes[0])
    assert result.config_error == "session_changed" and result.snapshots_inserted == 1
    assert snapshots(realm)[0].success and proof(realm) == before
    assert folder(realm).actual_paused is None


def test_identity_replacement_is_not_adopted(realm):
    realm.system_device = "different-device"
    result = collector(realm).observe(realm.nodes[0])
    assert result.node_error == "identity_mismatch" and not snapshots(realm)[0].success
    with realm.database.session() as session:
        assert session.get(ManagedNode, realm.nodes[0]).syncthing_device_id == "device-a"


def test_disappearing_and_returning_folder_keeps_uuid(realm):
    service = collector(realm)
    service.observe(realm.nodes[0])
    realm.clock.current += timedelta(seconds=31)
    realm.configs = []
    service.observe(realm.nodes[0])
    assert folder(realm).present is False and folder(realm).actual_paused is False
    realm.clock.current += timedelta(seconds=31)
    realm.configs = [("shared", "Back", True)]
    service.observe(realm.nodes[0])
    assert folder(realm).present and folder(realm).label == "Back"
    assert folder(realm).id == realm.folders[0] and folder(realm).actual_paused


def test_late_probe_is_historical_but_cannot_replace_newer_cache(realm):
    service = collector(realm)
    newer = realm.clock.current + timedelta(seconds=10)

    def concurrent():
        with realm.database.session() as session:
            node = session.get(ManagedNode, realm.nodes[0])
            node.last_checked_at, node.api_status = newer, "auth_error"
            row = session.get(Folder, realm.folders[0])
            row.actual_paused, row.config_observed_at = True, newer
            session.commit()

    realm.on_config = concurrent
    result = service.observe(realm.nodes[0])
    assert not result.cache_updated and result.snapshots_inserted == 5
    assert folder(realm).actual_paused is True and folder(realm).config_observed_at == newer


def test_disable_after_actual_probe_keeps_history_without_updating_cache(realm):
    def disabled():
        with realm.database.session() as session:
            session.get(ManagedNode, realm.nodes[0]).enabled = False
            session.commit()

    realm.on_config = disabled
    result = collector(realm).observe(realm.nodes[0])
    assert result.snapshots_inserted == 5 and not result.cache_updated
    assert folder(realm).actual_paused is None


def test_target_change_discards_foreign_observations(realm):
    def rotated():
        old = realm.settings.connections["node-a"]
        realm.settings.connections["node-a"] = old.model_copy(
            update={"api_key": type(old.api_key)("rotated-public-key-canary")}
        )

    realm.on_config = rotated
    result = collector(realm).observe(realm.nodes[0])
    assert result.outcome == "conflict" and snapshots(realm) == []


def test_observation_never_overwrites_concurrent_canonical_checkpoint(realm):
    def moved():
        with realm.database.session() as session:
            session.get(ManagedNode, realm.nodes[0]).last_event_id = 41
            session.commit()

    realm.on_config = moved
    assert collector(realm).observe(realm.nodes[0]).outcome == "stored"
    assert proof(realm)[2] == 41


@pytest.mark.parametrize("exists", [True, False])
def test_disabled_or_missing_node_has_no_observation(realm, exists):
    if exists:
        with realm.database.session() as session:
            session.get(ManagedNode, realm.nodes[0]).enabled = False
            session.commit()
    assert collector(realm).observe(realm.nodes[0] if exists else uuid4()).outcome == "skipped"
    assert snapshots(realm) == [] and realm.factories == []


def test_event_failure_does_not_turn_successful_node_probe_into_failure(realm):
    realm.failure = "auth_error"
    service = CollectionService(
        realm.settings,
        realm.database,
        realm.clock,
        factory_provider=realm.collector.factory_provider,
    )
    result = service.collect_node(realm.nodes[0])
    assert result.events.error_code == "auth_error" and result.observations.node_error is None
    assert len([r for r in snapshots(realm) if r.scope == "node" and r.success]) == 1


def test_due_pass_uses_monitoring_snapshots_and_does_not_invent_missed_ticks(realm):
    realm.clock.current = datetime.now(UTC)
    service = CollectionService(
        realm.settings,
        realm.database,
        realm.clock,
        factory_provider=realm.collector.factory_provider,
    )
    assert len(service.collect_once()) == 2
    count = len(snapshots(realm))
    assert service.collect_once() == () and len(snapshots(realm)) == count
    realm.clock.current += timedelta(seconds=95)
    assert len(service.collect_once()) == 2
    assert len([r for r in snapshots(realm) if r.scope == "node"]) == 4


def test_snapshot_transaction_rolls_back_cache_and_partial_rows(realm):
    with realm.database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION scc_fail_snapshot() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'public-secret-snapshot-canary'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER scc_fail_snapshot BEFORE INSERT ON status_snapshots "
                "FOR EACH ROW WHEN (NEW.scope='folder') EXECUTE FUNCTION scc_fail_snapshot()"
            )
        )
    try:
        result = collector(realm).observe(realm.nodes[0])
        assert result.error_code == "database_unavailable" and snapshots(realm) == []
        assert folder(realm).actual_paused is None
    finally:
        with realm.database.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER scc_fail_snapshot ON status_snapshots"))
            connection.execute(text("DROP FUNCTION scc_fail_snapshot()"))
