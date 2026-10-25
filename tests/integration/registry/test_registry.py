from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.contracts import dto
from app.contracts.errors import AppError
from app.contracts.interfaces import AdapterError
from app.main import create_app
from app.models import Folder, ManagedNode, SyncRule
from app.registry.admin import AdminRecord
from app.registry.authentication import AdministratorAuth
from app.registry.resolver import NodeResolver
from app.registry.security import PasswordHasher, SessionCodec
from app.syncthing_adapter import HttpAdapterFactory
from fastapi.testclient import TestClient
from sqlalchemy import func, select

pytestmark = pytest.mark.postgres


def register(realm, ref="node-a", name="Demo"):
    return realm.service.register(dto.NodeCreate(connection_ref=ref, display_name=name))


def checkpoint(realm, identifier):
    with realm.database.session() as session:
        row = session.get(ManagedNode, identifier)
        return (
            row.source_start_time_raw,
            row.source_session_id,
            row.source_stream_anchor,
            row.last_event_id,
        )


def test_two_nodes_share_external_folder_id_but_not_application_ids(realm):
    first, second = register(realm), register(realm, "node-b")
    a, b = realm.service.folders(first.id, 50, 0), realm.service.folders(second.id, 50, 0)
    assert {f.syncthing_folder_id for f in a.items} == {f.syncthing_folder_id for f in b.items}
    assert not {f.id for f in a.items} & {f.id for f in b.items}
    assert realm.service.nodes(1, 1).total == 2
    assert all(p.closed for p in realm.probes)
    target = NodeResolver(realm.settings, realm.database)(first.id)
    assert target.expected_device_id == "device-a" and target.checkpoint.anchor is not None
    assert target.checkpoint.node_id == first.id
    assert checkpoint(realm, first.id)[3] == 0


@pytest.mark.parametrize(
    "code,status,public_code",
    [
        ("timeout", 504, "upstream_timeout"),
        ("unreachable", 502, "upstream_unreachable"),
        ("auth_error", 502, "upstream_auth_error"),
        ("identity_mismatch", 502, "upstream_invalid_response"),
        ("invalid_response", 502, "upstream_invalid_response"),
        ("session_changed", 503, "service_unavailable"),
    ],
)
def test_failed_registration_is_atomic(realm, code, status, public_code):
    realm.sources["http://127.0.0.1:18384"].error = code
    with pytest.raises(AppError) as error:
        register(realm)
    assert (error.value.status_code, error.value.code) == (status, public_code)
    with realm.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ManagedNode)) == 0
        assert session.scalar(select(func.count()).select_from(Folder)) == 0
    assert all(p.closed for p in realm.probes)


def test_unknown_alias_does_not_contact_source(realm):
    with pytest.raises(AppError) as error:
        register(realm, "not-allowed")
    assert error.value.code == "connection_not_allowed" and not realm.calls


def test_registration_rejects_incomplete_or_changed_observation(realm):
    source = realm.sources["http://127.0.0.1:18384"]
    source.on_list = lambda: setattr(source, "generation", 2)
    with pytest.raises(AppError) as error:
        register(realm)
    assert error.value.status_code == 503
    assert realm.service.nodes(50, 0).total == 0


def test_duplicate_folder_list_cannot_create_partial_registry(realm):
    source = realm.sources["http://127.0.0.1:18384"]
    source.folders *= 2
    with pytest.raises(AppError) as error:
        register(realm)
    assert error.value.code == "upstream_invalid_response"
    assert realm.service.nodes(50, 0).total == 0


@pytest.mark.parametrize("change", ["removed", "rotated"])
def test_server_connection_changed_during_registration_is_not_persisted(realm, change):
    source = realm.sources["http://127.0.0.1:18384"]

    def mutate():
        if change == "removed":
            realm.settings.connections.pop("node-a", None)
        else:
            realm.settings.connections["node-a"].api_key = "rotated-public-key"

    source.on_list = mutate
    with pytest.raises(AppError) as error:
        register(realm)
    assert error.value.status_code == 503 and realm.service.nodes(50, 0).total == 0


def test_concurrent_duplicate_registration_commits_one_complete_node(realm):
    barrier = Barrier(2)
    realm.sources["http://127.0.0.1:18384"].on_list = lambda: barrier.wait(8)

    def attempt(_):
        try:
            register(realm)
            return 201
        except AppError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == [201, 409]
    with realm.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ManagedNode)) == 1
        assert session.scalar(select(func.count()).select_from(Folder)) == 2


def test_runtime_unknown_is_not_idle_or_zero_and_config_remains_available(realm):
    source = realm.sources["http://127.0.0.1:18384"]
    source.runtime_error = "invalid_response"
    source.folders = [("shared", "Shared", True)]
    node = register(realm)
    folder = realm.service.folders(node.id, 50, 0).items[0]
    assert folder.actual_paused is True and folder.runtime_state is None
    assert folder.need_bytes is None and folder.runtime_observed_at is None
    assert not folder.is_stale


@pytest.mark.parametrize(
    "failure,api_status",
    [
        ("unreachable", "unreachable"),
        ("timeout", "unreachable"),
        ("auth_error", "auth_error"),
        ("invalid_response", "invalid_response"),
        ("session_changed", "unknown"),
    ],
)
def test_offline_unknown_and_cache_preserve_values_and_checkpoint(realm, failure, api_status):
    node = register(realm)
    before = realm.service.folders(node.id, 50, 0)
    proof = checkpoint(realm, node.id)
    calls = len(realm.calls)
    realm.sources["http://127.0.0.1:18384"].error = failure
    assert realm.service.node(node.id).api_status == "ok" and len(realm.calls) == calls
    realm.clock.advance()
    current = realm.service.node(node.id)
    assert current.api_status == api_status and current.is_stale
    folders = realm.service.folders(node.id, 50, 0)
    assert folders.items[0].actual_paused == before.items[0].actual_paused
    assert folders.items[0].config_observed_at == before.items[0].config_observed_at
    assert folders.items[0].is_stale and checkpoint(realm, node.id) == proof


def test_identity_replacement_never_adopts_new_device_or_erases_folders(realm):
    node = register(realm)
    before = checkpoint(realm, node.id)
    realm.sources["http://127.0.0.1:18384"].device = "replacement-device"
    realm.clock.advance()
    current = realm.service.node(node.id)
    assert current.api_status == "identity_mismatch" and current.syncthing_device_id == "device-a"
    assert realm.service.folders(node.id, 50, 0).total == 2
    assert checkpoint(realm, node.id) == before


def test_disappear_return_and_restart_preserve_ids_and_canonical_checkpoint(realm):
    node = register(realm)
    before = realm.service.folders(node.id, 50, 0)
    with realm.database.session() as session:
        session.get(ManagedNode, node.id).last_event_id = 41
        session.commit()
    proof = checkpoint(realm, node.id)
    source = realm.sources["http://127.0.0.1:18384"]
    source.folders = []
    source.generation = 2
    realm.clock.advance()
    missing = realm.service.folders(node.id, 50, 0)
    assert all(not f.present and f.is_stale for f in missing.items)
    source.folders = [("shared", "Changed", True), ("control", "Control", False)]
    realm.clock.advance()
    returned = realm.service.folders(node.id, 50, 0)
    assert {f.id for f in before.items} == {f.id for f in returned.items}
    assert all(f.present for f in returned.items)
    assert checkpoint(realm, node.id) == proof


def test_disabled_patch_prevents_network_and_does_not_advance_cursor(realm):
    node = register(realm)
    proof = checkpoint(realm, node.id)
    calls = len(realm.calls)
    current = realm.service.patch(node.id, dto.NodePatch(enabled=False, display_name="Renamed"))
    realm.clock.advance()
    assert current.is_stale and not realm.service.node(node.id).enabled
    assert realm.service.folders(node.id, 50, 0).total == 2 and len(realm.calls) == calls
    assert checkpoint(realm, node.id) == proof
    with pytest.raises(AdapterError):
        NodeResolver(realm.settings, realm.database)(node.id)


def test_slow_observation_cannot_overwrite_concurrent_patch(realm):
    node = register(realm)
    entered, release = Event(), Event()
    source = realm.sources["http://127.0.0.1:18384"]

    def block():
        entered.set()
        assert release.wait(8)

    source.on_list = block
    realm.clock.advance()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(realm.service.refresh_node, node.id)
        try:
            assert entered.wait(8)
            realm.service.patch(node.id, dto.NodePatch(enabled=False))
        finally:
            release.set()
        task.result(timeout=8)
    assert not realm.service.node(node.id).enabled
    assert realm.service.node(node.id).last_checked_at == node.last_checked_at


def test_active_rule_projection_requires_injected_B1_evaluator(realm):
    node = register(realm)
    folder = realm.service.folders(node.id, 50, 0).items[0]
    with realm.database.session() as session:
        rule = SyncRule(
            folder_id=folder.id,
            days_mask=127,
            start_time=time(9),
            end_time=time(18),
            timezone="Europe/Moscow",
            enabled=True,
            version=1,
        )
        session.add(rule)
        session.commit()
    with pytest.raises(AppError) as error:
        realm.service.folders(node.id, 50, 0)
    assert error.value.code == "service_unavailable"
    realm.service.desired_state = lambda *_: True
    result = realm.service.folders(node.id, 50, 0)
    actual = next(f for f in result.items if f.id == folder.id)
    assert actual.desired_paused is True and actual.active_rule_id is not None


def test_resolver_restores_legacy_anchor_and_factory_invalidates_removed_alias(realm):
    node = register(realm)
    with realm.database.session() as session:
        row = session.get(ManagedNode, node.id)
        row.source_stream_anchor = None
        session.commit()
    resolver = NodeResolver(realm.settings, realm.database)
    assert resolver(node.id).checkpoint.anchor is None
    closed = []

    class Transport:
        def close(self):
            closed.append(True)

    factory = HttpAdapterFactory(resolver, transport_factory=lambda _: Transport())
    factory.for_node(node.id)
    del realm.settings.connections["node-a"]
    with pytest.raises(AdapterError):
        factory.for_node(node.id)
    assert closed == [True]
    factory.close()


def test_http_registry_uses_real_auth_guard_and_sql_without_secret_responses(realm):
    admin = AdminRecord(uuid4(), "admin", PasswordHasher.dummy_hash())
    auth = AdministratorAuth(
        realm.settings, SimpleNamespace(administrator=lambda: admin), realm.clock
    )
    cookie, claims = SessionCodec(realm.settings.session_secret.get_secret_value()).issue(
        admin.id, realm.clock.now
    )
    app = create_app(
        realm.settings,
        database=realm.database,
        clock=realm.clock,
        adapters=realm.factory,
        registry=realm.service,
        auth=auth,
    )
    with TestClient(app) as client:
        assert client.get("/api/nodes").status_code == 401
        client.cookies.set("scc_session", cookie)
        headers = {"Origin": "http://localhost:8000", "X-CSRF-Token": claims.csrf_token}
        connections = client.get("/api/connections?limit=1").json()
        assert connections["total"] == 2 and len(connections["items"]) == 1
        response = client.post(
            "/api/nodes",
            json={"connection_ref": "node-a", "display_name": "Alpha"},
            headers=headers,
        )
        assert response.status_code == 201
        identifier = response.json()["id"]
        assert client.get(f"/api/nodes/{identifier}/folders").json()["total"] == 2
        assert (
            client.patch(
                f"/api/nodes/{identifier}", json={"enabled": False}, headers=headers
            ).status_code
            == 200
        )
        error = client.get(f"/api/nodes/{uuid4()}")
        assert error.status_code == 404 and error.json()["error"]["code"] == "node_not_found"
        assert client.get("/api/nodes?limit=0").status_code == 422
        assert "public-registry-key-canary" not in response.text
        assert "base_url" not in response.text and "connection_ref" not in response.text
        UUID(identifier)


def test_old_capture_cannot_overwrite_a_newer_persisted_observation(realm):
    node = register(realm)
    realm.clock.advance()
    newer = realm.clock.now + timedelta(seconds=1)

    def concurrent_observation():
        with realm.database.session() as session:
            row = session.get(ManagedNode, node.id)
            row.last_checked_at = newer
            row.api_status = "auth_error"
            for folder in session.scalars(select(Folder).where(Folder.node_id == node.id)):
                folder.actual_paused = True
                folder.config_observed_at = newer
            session.commit()

    realm.sources["http://127.0.0.1:18384"].on_list = concurrent_observation
    realm.service.refresh_node(node.id)
    with realm.database.session() as session:
        row = session.get(ManagedNode, node.id)
        assert row.api_status == "auth_error" and row.last_checked_at == newer
        assert all(
            f.actual_paused
            for f in session.scalars(select(Folder).where(Folder.node_id == node.id))
        )


def test_valid_empty_folder_list_registers_a_complete_node(realm):
    realm.sources["http://127.0.0.1:18384"].folders = []
    node = register(realm)
    assert node.api_status == "ok" and realm.service.folders(node.id, 50, 0).total == 0


def test_default_application_composes_and_closes_registry_factory(realm):
    node = register(realm)
    app = create_app(realm.settings, database=realm.database, clock=realm.clock)
    with TestClient(app):
        factory = app.state.adapters
        assert isinstance(factory, HttpAdapterFactory)
        factory.for_node(node.id)  # Construction uses the DB resolver; performs no source I/O.
        assert isinstance(app.state.registry, type(realm.service))
    with pytest.raises(AdapterError):
        factory.for_node(node.id)
