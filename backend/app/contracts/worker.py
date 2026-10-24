"""Resources handed to the future B2 worker; this module does not schedule work."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.config import Settings
from app.contracts.interfaces import AdapterFactory, Clock
from app.contracts.locks import AdvisoryLocks, LockLease
from app.db import Database


@dataclass(frozen=True)
class WorkerContext:
    settings: Settings
    database: Database
    clock: Clock
    adapters: AdapterFactory
    folder_locks: AdvisoryLocks
    _singleton: LockLease

    def ensure_active(self) -> None:
        """B2 calls this before each cycle and before beginning a new external write."""
        self._singleton.ensure_held()


@runtime_checkable
class WorkerRuntime(Protocol):
    def run(self, context: WorkerContext) -> None:
        """Run B2's loop until stopped. A4 injects this implementation at composition."""
        ...
