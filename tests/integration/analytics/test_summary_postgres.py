import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from app.api.analytics import router
from app.api.auth import require_admin
from app.config import Settings
from app.contracts.dto import Analytics, User
from app.db import Database
from app.main import create_app
from app.models import AppUser, CommandExecution, Folder, ManagedNode, StatusSnapshot
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.postgres
PATH = "/api/analytics/summary"
START = datetime.fromisoformat("2026-11-04T23:30:00Z")
END = datetime.fromisoformat("2026-11-07T00:00:00Z")
PERIOD = {"from": "2026-11-04T23:30:00Z", "to": "2026-11-07T00:00:00Z"}
LATER = END - timedelta(minutes=1)


@pytest.fixture(scope="module")
def sample():
    value = os.environ.get("SCC_B4_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Real PostgreSQL: use tests/e2e/ui/check_postgres.py")
    url = make_url(value)
    if (
        os.environ.get("SCC_B4_TEST_PRIVATE_CLUSTER") != "b4-private-cluster"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or not url.port
        or url.port == 5432
        or url.database != "scc_b4_analytics"
        or url.username != "scc_test"
    ):
        pytest.fail("Refusing analytics tests outside the dedicated private PostgreSQL database")

    settings = Settings(database_url=value)
    database = Database(settings)
    database.check_ready()

    @event.listens_for(database.engine, "begin")
    def set_reporting_timezone(connection):
        connection.exec_driver_sql("SET LOCAL TIME ZONE 'Pacific/Honolulu'")

    admin = AppUser(id=uuid4(), login="b4-test", password_hash="test-only")
    a = ManagedNode(id=uuid4(), connection_ref="b4-a", display_name="Analytics A")
    b = ManagedNode(id=uuid4(), connection_ref="b4-b", display_name="Analytics B")
    folder_a = Folder(id=uuid4(), node_id=a.id, syncthing_folder_id="demo", label="Demo A")
    folder_b = Folder(id=uuid4(), node_id=b.id, syncthing_folder_id="demo", label="Demo B")

    def attempt(folder, status, at, *, source="rule", created=None):
        source_fields = (
            {"rule_version": 1, "rule_snapshot": {}}
            if source == "rule"
            else {"requested_by": admin.id, "idempotency_key": uuid4()}
        )
        evidence = {}
        if status == "applied":
            evidence = {
                "sent_at": at,
                "verified_at": at,
                "observed_paused": False,
                "restart_required": False,
                "source_session_id": "f" * 64,
            }
        return CommandExecution(
            node_id=folder.node_id,
            folder_id=folder.id,
            source=source,
            status=status,
            desired_paused=False,
            created_at=created or at,
            completed_at=None if status == "pending" else at,
            error_code=status if status in {"failed", "unknown", "cancelled"} else None,
            **source_fields,
            **evidence,
        )

    def probe(node, at, success, *, folder=None):
        return StatusSnapshot(
            node_id=node.id,
            folder_id=folder.id if folder else None,
            scope="folder" if folder else "node",
            observed_at=at,
            success=success,
            error_code=None if success else "unreachable",
        )

    app = create_app(settings, database, routers=(router,))
    app.dependency_overrides[require_admin] = lambda: User(id=admin.id, login=admin.login)
    with TestClient(app) as client:
        with database.session() as session:
            session.add_all([admin, a, b])
            session.flush()
            session.add_all([folder_a, folder_b])
            session.flush()
            earlier = START - timedelta(minutes=1)
            session.add_all(
                [
                    attempt(folder_a, "applied", START, created=earlier),
                    attempt(folder_a, "failed", LATER, source="manual"),
                    attempt(folder_a, "unknown", LATER),
                    attempt(folder_b, "cancelled", START),
                    attempt(folder_a, "pending", LATER),
                    attempt(folder_b, "pending", earlier),
                    attempt(folder_b, "applied", END, created=LATER),
                    attempt(folder_a, "failed", earlier),
                    probe(a, START, True),
                    probe(a, LATER, True),
                    probe(a, LATER, False),
                    probe(b, START, False),
                    probe(a, START, True, folder=folder_a),
                    probe(a, START, False, folder=folder_a),
                    probe(b, END, False),
                    probe(b, earlier, True),
                ]
            )
            session.commit()
        yield client, a.id, b.id


def test_daily_and_total_match_manual_calculation(sample):
    client, _, _ = sample
    response = client.get(PATH, params=PERIOD)
    assert response.status_code == 200
    summary = Analytics.model_validate_json(response.content)
    assert summary.commands.model_dump() == {
        "applied": 1,
        "failed": 1,
        "unknown": 1,
        "cancelled": 1,
        "pending_created": 1,
        "success_ratio": 0.5,
    }
    assert summary.api_checks.model_dump() == {
        "successful_checks": 2,
        "total_checks": 4,
        "success_ratio": 0.5,
    }
    assert [point.date.isoformat() for point in summary.daily] == [
        "2026-11-04",
        "2026-11-05",
        "2026-11-06",
    ]
    first, empty, last = summary.daily
    assert first.commands.success_ratio == 1
    assert first.commands.cancelled == 1
    assert first.api_checks.total_checks == 2
    assert empty.commands.success_ratio is None
    assert empty.api_checks.success_ratio is None
    assert last.commands.success_ratio == 0
    assert last.commands.unknown == last.commands.pending_created == 1
    assert last.api_checks.success_ratio == 0.5


def test_node_applies_to_both_and_source_only_to_commands(sample):
    client, a, b = sample
    response = client.get(PATH, params=PERIOD | {"node_id": str(a), "source": "manual"})
    assert response.status_code == 200
    summary = Analytics.model_validate_json(response.content)
    assert (summary.node_id, summary.source) == (a, "manual")
    assert (summary.commands.applied, summary.commands.failed) == (0, 1)
    assert summary.api_checks.total_checks == 3
    assert summary.api_checks.success_ratio == pytest.approx(2 / 3)

    response = client.get(PATH, params=PERIOD | {"node_id": str(b), "source": "rule"})
    assert response.status_code == 200
    summary = Analytics.model_validate_json(response.content)
    assert summary.commands.cancelled == 1
    assert summary.commands.success_ratio is None
    assert summary.api_checks.success_ratio == 0


def test_31_days_can_touch_32_utc_dates_without_any_samples(sample):
    client, _, _ = sample
    response = client.get(
        PATH,
        params={"from": "2026-01-01T00:01:00Z", "to": "2026-02-01T00:01:00Z"},
    )
    assert response.status_code == 200
    summary = Analytics.model_validate_json(response.content)
    assert len(summary.daily) == 32
    assert summary.commands.success_ratio is None
    assert summary.api_checks.success_ratio is None
