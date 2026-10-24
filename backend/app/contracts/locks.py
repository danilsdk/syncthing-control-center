"""Session advisory locks pinned across commits; no locked connection returns to the pool."""

import hashlib
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


class LockBusy(Exception):
    def __init__(self) -> None:
        super().__init__("Operation is already in progress")


class LockLost(Exception):
    def __init__(self) -> None:
        super().__init__("Operation lock is unavailable")


def folder_lock_key(folder_id: UUID) -> int:
    if not isinstance(folder_id, UUID):
        raise TypeError("Expected an internal folder UUID")
    digest = hashlib.sha256(f"scc:folder:v1\n{folder_id}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@dataclass(frozen=True)
class _Key:
    arguments: dict[str, int]
    expression: str
    high: int
    low: int
    sub_id: int


def _folder_key(folder_id: UUID) -> _Key:
    value = folder_lock_key(folder_id)
    unsigned = value & ((1 << 64) - 1)
    return _Key({"key": value}, ":key", unsigned >> 32, unsigned & 0xFFFFFFFF, 1)


# PostgreSQL's (int,int) and bigint advisory lock spaces are disjoint. This singleton
# cannot collide with any folder key, including a hashed UUID collision.
_WORKER_KEY = _Key({"namespace": 1935893248, "worker": 1}, ":namespace, :worker", 1935893248, 1, 2)


class LockLease:
    def __init__(self, connection: Connection, key: _Key, backend_pid: int) -> None:
        self._connection = connection
        self._key = key
        self.backend_pid = backend_pid
        self._session_active = False

    def ensure_held(self) -> None:
        connection = self._connection
        if connection.closed or connection.invalidated:
            raise LockLost()
        in_transaction = connection.in_transaction()
        try:
            pid, held = connection.execute(
                text(
                    "SELECT pg_backend_pid(), EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                    "AND classid::bigint = :high AND objid::bigint = :low AND objsubid = :sub_id)"
                ),
                {"high": self._key.high, "low": self._key.low, "sub_id": self._key.sub_id},
            ).one()
            if not in_transaction:
                connection.commit()
            if pid != self.backend_pid or not held:
                connection.invalidate()
                raise LockLost()
        except SQLAlchemyError:
            connection.invalidate()
            raise LockLost() from None

    @contextmanager
    def session(self) -> Iterator[Session]:
        """All protected DB reads/writes use this pinned connection, including sent_at commit.

        Call ensure_held() again immediately before an external write. A lease is
        single-threaded and must not be stored after its enclosing context exits.
        """
        if self._session_active:
            raise RuntimeError("A lease cannot open nested sessions")
        self.ensure_held()
        self._session_active = True
        try:
            with Session(bind=self._connection, expire_on_commit=False) as session:
                yield session
        finally:
            self._session_active = False


class AdvisoryLocks:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def folder(self, folder_id: UUID, *, timeout_seconds: float = 3) -> Iterator[LockLease]:
        with self._hold(_folder_key(folder_id), timeout_seconds=timeout_seconds) as lease:
            yield lease

    @contextmanager
    def worker(self) -> Iterator[LockLease]:
        with self._hold(_WORKER_KEY, timeout_seconds=0) as lease:
            yield lease

    @contextmanager
    def _hold(self, key: _Key, *, timeout_seconds: float) -> Iterator[LockLease]:
        if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 30:
            raise ValueError("Lock timeout must be finite and between 0 and 30 seconds")
        if self.engine.dialect.name != "postgresql":
            raise LockLost()
        connection = None
        acquired = False
        body_failed = False
        entered_body = False
        try:
            connection = self.engine.connect()
            deadline = time.monotonic() + timeout_seconds
            while True:
                acquired, pid = connection.execute(
                    text(f"SELECT pg_try_advisory_lock({key.expression}), pg_backend_pid()"),
                    key.arguments,
                ).one()
                connection.commit()  # Session lock survives this and every service commit.
                if acquired:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockBusy()
                time.sleep(min(0.05, remaining))
            entered_body = True
            yield LockLease(connection, key, pid)
        except SQLAlchemyError:
            body_failed = True
            if entered_body:
                raise  # Keep service constraint errors available to B1/B2.
            raise LockLost() from None
        except BaseException:
            body_failed = True
            raise
        finally:
            if connection is not None:
                try:
                    if acquired:
                        if connection.closed or connection.invalidated:
                            raise LockLost()
                        connection.rollback()  # An aborted transaction cannot execute unlock.
                        released = connection.scalar(
                            text(f"SELECT pg_advisory_unlock({key.expression})"), key.arguments
                        )
                        connection.commit()
                        if released is not True:
                            raise LockLost()
                except (SQLAlchemyError, LockLost):
                    # Rollback alone does not release a session advisory lock. Discard the
                    # physical connection if unlock cannot be confirmed, never pool it.
                    connection.invalidate()
                    if not body_failed:
                        raise LockLost() from None
                finally:
                    connection.close()
