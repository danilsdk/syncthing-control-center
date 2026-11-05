"""Registry pages remain consistent across independent PostgreSQL commits."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.contracts import dto
from app.main import create_app
from app.models import Folder, ManagedNode
from app.registry.admin import AdminRecord
from app.registry.authentication import AdministratorAuth
from app.registry.security import PasswordHasher, SessionCodec
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, insert, select

pytestmark = pytest.mark.postgres


@pytest.fixture
def client(realm):
    admin = AdminRecord(uuid4(), "admin", PasswordHasher.dummy_hash())
    auth = AdministratorAuth(
        realm.settings, SimpleNamespace(administrator=lambda: admin), realm.clock
    )
    cookie, _ = SessionCodec(realm.settings.session_secret.get_secret_value()).issue(
        admin.id, realm.clock.now_utc()
    )
    app = create_app(
        realm.settings,
        database=realm.database,
        clock=realm.clock,
        adapters=realm.factory,
        registry=realm.service,
        auth=auth,
    )
    with TestClient(app) as value:
        value.cookies.set("scc_session", cookie)
        yield value


@contextmanager
def concurrent_commit(realm, table, statement):
    committed = []

    def after_count(connection, _cursor, sql, _params, _context, _many):
        normalized = " ".join(sql.lower().split())
        if committed or "select count(" not in normalized or table not in normalized:
            return
        committed.append(True)
        with realm.database.engine.begin() as writer:
            assert writer is not connection
            writer.execute(statement)

    event.listen(realm.database.engine, "after_cursor_execute", after_count)
    try:
        yield
        assert committed, "The concurrent writer was not exercised"
    finally:
        event.remove(realm.database.engine, "after_cursor_execute", after_count)


@pytest.mark.parametrize("resource", ["nodes", "folders"])
@pytest.mark.parametrize("operation", ["insert", "delete"])
def test_page_uses_one_snapshot_during_a_concurrent_commit(client, realm, resource, operation):
    identifier = uuid4()
    if resource == "nodes":
        path, table = "/api/nodes", "managed_nodes"
        addition = insert(ManagedNode).values(
            id=identifier, connection_ref="node-a", display_name="Alpha"
        )
        removal = delete(ManagedNode).where(ManagedNode.id == identifier)
    else:
        realm.sources["http://127.0.0.1:18384"].folders = []
        node = realm.service.register(dto.NodeCreate(connection_ref="node-a", display_name="Alpha"))
        path, table = f"/api/nodes/{node.id}/folders", "folders"
        addition = insert(Folder).values(
            id=identifier, node_id=node.id, syncthing_folder_id="shared", label="Shared"
        )
        removal = delete(Folder).where(Folder.id == identifier)
    if operation == "delete":
        with realm.database.engine.begin() as writer:
            writer.execute(addition)
    initial_count = int(operation == "delete")
    with concurrent_commit(realm, table, addition if operation == "insert" else removal):
        response = client.get(path)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == len(page["items"]) == initial_count
    if initial_count:
        assert page["items"][0]["id"] == str(identifier)
    current = client.get(path)
    assert current.status_code == 200, current.text
    assert current.json()["total"] == len(current.json()["items"]) == 1 - initial_count


def test_empty_pages_totals_order_and_folder_scope(client, realm):
    empty = client.get("/api/nodes?offset=100")
    assert empty.status_code == 200 and empty.json()["total"] == 0
    assert empty.json()["items"] == []
    first = realm.service.register(dto.NodeCreate(connection_ref="node-a", display_name="Same"))
    second = realm.service.register(dto.NodeCreate(connection_ref="node-b", display_name="Same"))
    nodes = client.get("/api/nodes").json()
    assert [item["id"] for item in nodes["items"]] == sorted([str(first.id), str(second.id)])
    folders_path = f"/api/nodes/{first.id}/folders"
    folders = client.get(folders_path).json()
    other_folders = client.get(f"/api/nodes/{second.id}/folders").json()
    assert not {f["id"] for f in folders["items"]} & {f["id"] for f in other_folders["items"]}
    with realm.database.session() as session:
        # Equal timestamps exercise the UUID tie-break without relying on timing.
        for row in session.scalars(select(Folder).where(Folder.node_id == first.id)):
            row.created_at = realm.clock.now_utc()
        session.commit()
    folders = client.get(folders_path).json()
    assert [f["id"] for f in folders["items"]] == sorted(
        [f["id"] for f in folders["items"]], reverse=True
    )
    for path, full in [("/api/nodes", nodes), (folders_path, folders)]:
        assert full["total"] == 2
        for offset in (0, 1, 2, 100):
            response = client.get(path, params={"limit": 1, "offset": offset})
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total"] == 2 and page["limit"] == 1 and page["offset"] == offset
            assert page["items"] == full["items"][offset : offset + 1]
