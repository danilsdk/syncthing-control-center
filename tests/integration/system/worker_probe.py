"""Disposable container test controls; no product route or arbitrary SQL execution."""

import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from uuid import UUID


def main():
    sys.path.insert(0, "/opt/deploy")
    from entrypoint import ownership, settings

    owner = ownership()
    if not owner["project"].startswith("scc-a4-test-"):
        raise ValueError("Worker probe requires a disposable owned project")
    args = json.loads(sys.stdin.buffer.read(16385))
    nonce = args["nonce"]
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("Invalid worker control nonce")
    control = Path("/tmp") / (owner["project"] + "-" + nonce)
    if control.resolve().parent != Path("/tmp"):
        raise ValueError("Redirected worker control directory")
    stamp = control / "owner.json"
    if args["mode"] == "create":
        control.mkdir(mode=0o700)
        stamp.write_text(json.dumps(owner))
    elif not stamp.is_file() or json.loads(stamp.read_text()) != owner:
        raise ValueError("Foreign worker control directory")
    mode = args["mode"]
    if mode in {"create", "control"}:
        action = args.get("action", "status")
        if action in {"gate-release", "stop"}:
            (control / action).touch()
        elif action != "status":
            raise ValueError("Unsupported control action")
        audit = control / "operations.log"
        print(
            json.dumps(
                {
                    "ready": (control / "gate-ready").exists(),
                    "operations": len(audit.read_text().splitlines()) if audit.exists() else 0,
                }
            ),
            flush=True,
        )
        return 0
    if mode in {"state", "lose", "hold-folder"}:
        from app.contracts.locks import AdvisoryLocks
        from app.db import Database
        from app.models import Folder, ManagedNode
        from sqlalchemy import text

        database = Database(settings())
        try:
            database.check_ready()
            if mode == "hold-folder":
                folder_id = UUID(args["folder_id"])
                with database.session() as session:
                    folder = session.get(Folder, folder_id)
                    if folder is None or folder.syncthing_folder_id != "scc-a4-shared":
                        raise ValueError("Lock probe requires the shared demo folder")
                    node = session.get(ManagedNode, folder.node_id)
                    if node is None or node.connection_ref != "demo-a":
                        raise ValueError("Lock probe requires demo-a")
                with AdvisoryLocks(database.engine).folder(folder_id, timeout_seconds=0):
                    (control / "gate-ready").touch()
                    until = time.monotonic() + 100
                    while not (control / "gate-release").exists():
                        if time.monotonic() >= until:
                            raise RuntimeError("Owned folder lock gate timed out")
                        time.sleep(0.05)
                return 0

            with database.session() as session:
                pids = list(
                    session.scalars(
                        text(
                            "SELECT pid FROM pg_locks WHERE locktype='advisory' "
                            "AND classid::bigint=1935893248 AND objid::bigint=1 AND objsubid=2 "
                            "AND database=(SELECT oid FROM pg_database "
                            "WHERE datname=current_database())"
                        )
                    )
                )
                if mode == "lose":
                    if len(pids) != 1:
                        raise ValueError("Expected exactly one owned singleton")
                    stopped = session.scalar(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": pids[0]}
                    )
                    print(json.dumps({"terminated": bool(stopped)}), flush=True)
                else:
                    counts = {
                        name: session.scalar(text("SELECT count(*) FROM " + name))
                        for name in ("sync_events", "status_snapshots", "command_executions")
                    }
                    proof = session.execute(
                        text(
                            "SELECT id,last_event_id,source_session_id,"
                            "source_stream_anchor,last_checked_at "
                            "FROM managed_nodes ORDER BY id"
                        )
                    ).all()
                    print(
                        json.dumps(
                            {
                                "workers": len(pids),
                                "worker_pids": pids,
                                "counts": counts,
                                "proof": hashlib.sha256(
                                    json.dumps([list(row) for row in proof], default=str).encode()
                                ).hexdigest(),
                            }
                        ),
                        flush=True,
                    )
        finally:
            database.close()
        return 0
    if mode != "run":
        raise ValueError("Unsupported probe mode")
    import runtime

    class FileStop(threading.Event):
        def is_set(self):
            return super().is_set() or (control / "stop").exists()

        def wait(self, timeout):
            deadline = time.monotonic() + timeout
            while not self.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                super().wait(min(remaining, 0.05))
            return True

    original, reached = runtime.HttpTransport, [False]

    def record(name):
        with (control / "operations.log").open("a") as output:
            output.write(name + "\n")

    class GatedTransport(original):
        def request(self, method, path, **options):
            record("http_request")
            result = super().request(method, path, **options)
            if path.startswith("/rest/events") and not reached[0]:
                reached[0] = True
                (control / "gate-ready").touch()
                deadline = time.monotonic() + 45
                while not (control / "gate-release").exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Owned phase gate timed out")
                    time.sleep(0.05)
            return result

    runtime.HttpTransport = GatedTransport
    collector = runtime.CollectionService

    class GatedCollection(collector):
        def collect_once(self):
            # Reach the first source-read gate independently of recently cached probes.
            return super().collect_once(force=True)

    runtime.CollectionService = GatedCollection
    return runtime.worker_main(settings(), stop_event=FileStop())


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        print("Owned worker probe failed; private values were not printed", file=sys.stderr)
        result = 1
    raise SystemExit(result)
