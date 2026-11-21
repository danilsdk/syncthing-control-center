"""A4's single sequential worker: accepted A3 collection and bounded B2 passes."""

import argparse
import signal
import sys
import threading
import time
from dataclasses import replace

from app.collection import CollectionService
from app.config import Settings
from app.contracts.interfaces import SystemClock
from app.db import Database
from app.main import run_worker
from app.reconciliation.service import run_once
from app.registry.resolver import NodeResolver
from app.syncthing_adapter import HttpAdapterFactory
from app.syncthing_adapter.transport import HttpTransport
from sqlalchemy import event


class WorkerStopping(Exception):
    """Internal graceful shutdown, never an adapter outcome or success observation."""


class Activity:
    def __init__(self, context, stop):
        self.context, self.stop = context, stop
        self.checking = False

    def check(self, *_args):
        # The singleton check itself uses this engine. Do not recursively check its SQL.
        if self.checking:
            return
        self.checking = True
        try:
            if self.stop.is_set():
                raise WorkerStopping()
            self.context.ensure_active()
            if self.stop.is_set():
                raise WorkerStopping()
        finally:
            self.checking = False


class ActiveAdapter:
    def __init__(self, adapter, activity):
        self.adapter, self.activity = adapter, activity

    def __getattr__(self, name):
        operation = getattr(self.adapter, name)
        if not callable(operation) or name == "close":
            return operation

        def checked(*args, **kwargs):
            self.activity.check()
            return operation(*args, **kwargs)

        return checked


class ActiveFactory:
    def __init__(self, factory, activity):
        self.factory, self.activity = factory, activity

    def for_node(self, node_id):
        self.activity.check()
        return ActiveAdapter(self.factory.for_node(node_id), self.activity)

    def close(self):
        # Resource cleanup remains permitted after stop or lease loss.
        self.factory.close()


class ActiveTransport:
    def __init__(self, transport, activity):
        self.transport, self.activity = transport, activity

    def request(self, *args, **kwargs):
        # A single adapter method may issue several proof/config requests.
        self.activity.check()
        return self.transport.request(*args, **kwargs)

    def close(self):
        self.transport.close()


class ApplicationRuntime:
    def __init__(self, stop_event=None, *, once=False, monotonic=time.monotonic):
        self.stop = stop_event if stop_event is not None else threading.Event()
        self.once, self.monotonic = once, monotonic
        self.activity = None

    def transport(self, connection):
        if self.activity is None:
            raise RuntimeError("Worker context is not active")
        self.activity.check()
        return ActiveTransport(HttpTransport(connection), self.activity)

    def run(self, context):
        activity = Activity(context, self.stop)
        self.activity = activity
        guarded = replace(context, adapters=ActiveFactory(context.adapters, activity))

        def collection_factory():
            activity.check()
            # A3 owns and closes each factory; B2 keeps the worker's separate factory.
            return ActiveFactory(
                HttpAdapterFactory(
                    NodeResolver(context.settings, context.database),
                    clock=context.clock,
                    transport_factory=self.transport,
                ),
                activity,
            )

        collector = CollectionService(
            context.settings, context.database, context.clock, factory_provider=collection_factory
        )
        engine = context.database.engine
        # A3 deliberately catches source failures. Gate SQL/commit as well as HTTP so
        # swallowed LockLost cannot become a later cached observation or checkpoint.
        event.listen(engine, "before_cursor_execute", activity.check)
        event.listen(engine, "commit", activity.check)
        observation_due = reconciliation_due = self.monotonic()
        try:
            while not self.stop.is_set():
                activity.check()
                if self.monotonic() >= observation_due:
                    collector.collect_once()
                    activity.check()
                    observation_due = (
                        self.monotonic() + context.settings.observation_interval_seconds
                    )
                if self.monotonic() >= reconciliation_due:
                    run_once(replace(guarded, _singleton=_ActivityLease(activity)))
                    activity.check()
                    reconciliation_due = (
                        self.monotonic() + context.settings.reconciliation_interval_seconds
                    )
                if self.once:
                    return
                self.stop.wait(max(0, min(observation_due, reconciliation_due) - self.monotonic()))
        except WorkerStopping:
            return
        finally:
            event.remove(engine, "before_cursor_execute", activity.check)
            event.remove(engine, "commit", activity.check)


class _ActivityLease:
    def __init__(self, activity):
        self.activity = activity

    def ensure_held(self):
        self.activity.check()


def worker_main(settings, *, once=False, stop_event=None):
    if settings.db_pool_size < 3:
        print("Shared worker requires a database pool size of at least 3", file=sys.stderr)
        return 2
    runtime = ApplicationRuntime(stop_event, once=once)
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop.set())
    database = factory = None
    try:
        database = Database(settings)
        clock = SystemClock()
        factory = HttpAdapterFactory(
            NodeResolver(settings, database), clock=clock, transport_factory=runtime.transport
        )
        return run_worker(
            runtime, settings=settings, database=database, clock=clock, adapters=factory
        )
    except Exception:
        # Configuration can contain credentials; never echo the exception.
        return run_worker(None, database=database, adapters=factory)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        result = worker_main(Settings(), once=args.once)
    except Exception:
        print("Worker configuration is invalid", file=sys.stderr)
        result = 2
    raise SystemExit(result)
