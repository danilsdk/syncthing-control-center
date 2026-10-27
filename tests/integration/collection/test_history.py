from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.collection import ObservationCollector
from app.collection.history import HistoryFilter, HistoryService
from app.contracts.errors import AppError
from app.models import ManagedNode, SyncEvent
from fastapi.testclient import TestClient
from sqlalchemy import event

from .conftest import packet
from .test_history_api import application

pytestmark = pytest.mark.postgres


def test_received_time_and_half_open_boundaries(realm):
    at = datetime(2026, 10, 26, tzinfo=UTC)
    realm.clock.current = at
    realm.collector.collect(realm.nodes[0])
    history = HistoryService(realm.database)
    assert history.events(HistoryFilter(from_at=at, to_at=at + timedelta(seconds=1))).total == 3
    assert history.events(HistoryFilter(to_at=at)).total == 0
    assert history.events(HistoryFilter(from_at=at + timedelta(seconds=1))).total == 0


def test_stable_sort_pagination_and_counts_after_filters(realm):
    realm.clock.current = datetime.now(UTC)
    realm.collector.collect(realm.nodes[0])
    history = HistoryService(realm.database)
    all_rows = history.events(HistoryFilter())
    assert [row.id for row in all_rows.items] == sorted(
        (r.id for r in all_rows.items), reverse=True
    )
    page = history.events(HistoryFilter(limit=1, offset=1, folder_id=realm.folders[0]))
    assert page.total == 3 and page.items[0].id == all_rows.items[1].id
    assert history.events(HistoryFilter(offset=99)).total == 3
    assert (
        history.events(HistoryFilter(node_id=realm.nodes[1], folder_id=realm.folders[0])).total == 0
    )
    assert history.events(HistoryFilter(folder_id=uuid4())).total == 0


def test_snapshot_scope_and_disabled_history_are_read_only(realm):
    ObservationCollector(
        realm.settings,
        realm.database,
        realm.clock,
        factory_provider=realm.collector.factory_provider,
    ).observe(realm.nodes[0])
    with realm.database.session() as session:
        session.get(ManagedNode, realm.nodes[0]).enabled = False
        session.commit()
    history = HistoryService(realm.database)
    before = len(realm.factories)
    assert history.snapshots(HistoryFilter(scope="node")).total == 1
    assert history.snapshots(HistoryFilter(scope="folder")).total == 4
    assert history.snapshots(HistoryFilter(scope="node", folder_id=realm.folders[0])).total == 0
    assert history.snapshots(HistoryFilter(folder_id=realm.folders[0])).total == 2
    assert len(realm.factories) == before


def test_count_and_page_are_from_one_consistent_snapshot(realm):
    realm.collector.collect(realm.nodes[0])
    done = False

    def concurrent(_conn, _cursor, statement, *_):
        nonlocal done
        if not done and "count(" in statement.lower() and "sync_events" in statement:
            done = True
            point = realm.store.load(realm.nodes[0])
            assert realm.store.save(point, packet(realm.nodes[0], 4)).outcome == "stored"

    event.listen(realm.database.engine, "after_cursor_execute", concurrent)
    try:
        page = HistoryService(realm.database).events(HistoryFilter())
    finally:
        event.remove(realm.database.engine, "after_cursor_execute", concurrent)
    assert done and page.total == len(page.items) == 3
    assert HistoryService(realm.database).events(HistoryFilter()).total == 4


def test_incomplete_stored_marker_is_a_safe_unavailable_history(realm):
    with realm.database.session() as session:
        session.add(
            SyncEvent(
                node_id=realm.nodes[0],
                kind="gap",
                source_session_id="a" * 64,
                event_type="HistoryGap",
                payload_summary={"state": "public-private-path-canary"},
            )
        )
        session.commit()
    with pytest.raises(AppError) as error:
        HistoryService(realm.database).events(HistoryFilter())
    assert error.value.code == "service_unavailable" and "canary" not in str(error.value)


def test_real_sql_is_available_through_guarded_http_router(realm):
    realm.collector.collect(realm.nodes[0])
    app, cookie = application(realm.database)
    with TestClient(app) as client:
        assert client.get("/api/events").status_code == 401
        client.cookies.set("scc_session", cookie)
        response = client.get(f"/api/events?node_id={realm.nodes[0]}&limit=1")
        assert response.status_code == 200 and response.json()["total"] == 3
        assert len(response.json()["items"]) == 1
        assert client.get("/api/snapshots?scope=node").json()["total"] == 0
        assert client.get(f"/api/events?folder_id={uuid4()}").json()["total"] == 0
