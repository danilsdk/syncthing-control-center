import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.contracts.locks import AdvisoryLocks, LockBusy, LockLost
from app.models import AppUser, CommandExecution
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from .test_postgres_schema import NOW, execution_values, seed

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[3]


def test_folder_lock_survives_sent_commit_and_uses_one_physical_session(pg):
    locks = AdvisoryLocks(pg)
    graph = seed(pg)
    folder = graph.folder
    with locks.folder(folder) as lease:
        with lease.session() as session:
            identity = session.scalar(
                insert(CommandExecution)
                .values(**execution_values(graph, sent_at=NOW))
                .returning(CommandExecution.id)
            )
            assert session.scalar(text("SELECT pg_backend_pid()")) == lease.backend_pid
            session.commit()
        with pg.connect() as connection:
            assert (
                connection.scalar(
                    select(CommandExecution.sent_at).where(CommandExecution.id == identity)
                )
                == NOW
            )
        lease.ensure_held()
        with pytest.raises(LockBusy), locks.folder(folder, timeout_seconds=0):
            pytest.fail("Another connection acquired the same folder after a commit")
        with locks.folder(uuid4(), timeout_seconds=0) as other:
            assert other.backend_pid != lease.backend_pid
        with lease.session() as session:
            assert session.scalar(text("SELECT pg_backend_pid()")) == lease.backend_pid
            with pytest.raises(RuntimeError, match="nested sessions"), lease.session():
                pytest.fail("Nested sessions can interfere with the outer transaction")
            session.rollback()
        lease.ensure_held()
    with locks.folder(folder, timeout_seconds=0):
        pass
    with pytest.raises(LockLost):
        lease.ensure_held()


def test_aborted_transaction_is_rolled_back_and_releases_lock_without_masking_error(pg):
    locks = AdvisoryLocks(pg)
    folder = uuid4()
    with pytest.raises(IntegrityError), locks.folder(folder) as lease, lease.session() as session:
        session.execute(insert(AppUser).values(login="same", password_hash="demo"))
        session.execute(insert(AppUser).values(login="same", password_hash="demo"))
    with locks.folder(folder, timeout_seconds=0) as lease, lease.session() as session:
        assert session.scalar(select(AppUser.id)) is None  # Both uncommitted inserts rolled back.


def test_connection_loss_fails_closed_and_does_not_reconnect_under_the_lease(pg):
    locks = AdvisoryLocks(pg)
    folder = uuid4()
    with pytest.raises(LockLost), locks.folder(folder) as lease:
        with pg.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": lease.backend_pid}
                )
                is True
            )
        with pytest.raises(LockLost):
            lease.ensure_held()
        with pytest.raises(LockLost), lease.session():
            pytest.fail("A lost lease must not silently reconnect")
    with locks.folder(folder, timeout_seconds=0):
        pass


def test_returned_pool_connections_have_no_advisory_locks(pg):
    locks = AdvisoryLocks(pg)
    with locks.folder(uuid4()):
        pass
    with locks.worker():
        with pytest.raises(LockBusy), locks.worker():
            pytest.fail("A second worker acquired the singleton")
        # Worker and folder lock namespaces are independent.
        with locks.folder(UUID(int=0), timeout_seconds=0):
            pass
    connections = []
    try:
        for _ in range(5):
            connection = pg.connect()
            connections.append(connection)
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE "
                        "locktype='advisory' AND pid=pg_backend_pid()"
                    )
                )
                == 0
            )
    finally:
        for connection in connections:
            connection.close()


def test_second_worker_process_exits_without_calling_runtime(pg):
    child = """
import os
from app.config import Settings
from app.main import run_worker
class Factory:
    def close(self): pass
    def for_node(self, node_id): raise AssertionError("Adapter must not be called")
class Worker:
    def run(self, context): raise AssertionError("Runtime must not be called")
settings = Settings(database_url=os.environ["SCC_TEST_DATABASE_URL"])
raise SystemExit(run_worker(Worker(), settings=settings, adapters=Factory()))
"""
    with AdvisoryLocks(pg).worker():
        result = subprocess.run(
            [sys.executable, "-c", child],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    assert result.returncode == 3
    assert "A worker is already running" in result.stderr
    assert "Traceback" not in result.stderr
