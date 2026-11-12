"""A3.5: real API, PostgreSQL and two native sources; no service/HTTP/clock doubles."""

import argparse
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "deploy/syncthing"))

import bench  # noqa: E402
from app.collection import EventStore  # noqa: E402
from app.config import Settings  # noqa: E402
from app.contracts import dto  # noqa: E402
from app.contracts.interfaces import AdapterError, NodeConnection, SystemClock  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import AppUser, Folder, ManagedNode, StatusSnapshot, SyncEvent  # noqa: E402
from app.registry.admin import create_administrator  # noqa: E402
from app.syncthing_adapter import HttpSyncthingAdapter  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

# Reuse the preceding stage's bounded HTTP client, not its scenarios or dependencies.
spec = importlib.util.spec_from_file_location(
    "registry_live_helpers", ROOT / "tests/integration/registry/check_live.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
require, wait_for = helpers.require, helpers.wait_for


class Api(helpers.Api):
    def start(self, connections):
        require(self.process is None, "API already started")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        with self.log.open("ab") as output:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "live_app:create_live_app",
                    "--factory",
                    "--app-dir",
                    "tests/integration/collection",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--no-access-log",
                    "--no-proxy-headers",
                ],
                cwd=ROOT,
                env={**self.env, "SCC_CONNECTIONS": json.dumps(connections)},
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=helpers.HIDDEN,
            )

        def ready():
            require(self.process.poll() is None, "Owned API exited before readiness")
            try:
                return self.request("GET", "/health/ready", expected=None)[0] == 200
            except OSError:
                return False

        wait_for(ready)


def ports_free():
    try:
        for _, api_port, sync_port in bench.NODES:
            for port in (api_port, sync_port):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def node_counts(database):
    with database.session() as session:
        return dict(
            session.execute(
                select(StatusSnapshot.node_id, func.count())
                .where(StatusSnapshot.scope == "node")
                .group_by(StatusSnapshot.node_id)
            ).all()
        )


def folder_state(database, node_id):
    with database.session() as session:
        return {
            row.syncthing_folder_id: (
                row.id,
                row.present,
                row.actual_paused,
                row.config_observed_at,
                row.runtime_state,
                row.need_bytes,
                row.pull_errors,
                row.runtime_observed_at,
            )
            for row in session.scalars(select(Folder).where(Folder.node_id == UUID(node_id)))
        }


def checkpoint(database, node_id):
    return helpers.checkpoint(database, node_id)


def cycle(env, identifiers=None, *, force=False):
    command = [sys.executable, "tests/integration/collection/live_worker.py"]
    for identifier in identifiers or []:
        command.extend(["--node", identifier])
    if force:
        command.append("--force")
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=70,
        creationflags=helpers.HIDDEN,
    )
    require(result.returncode == 0, "Private worker process failed")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise bench.BenchError("Private worker returned invalid summary") from None
    require(not result.stderr, "Private worker emitted unexpected diagnostics")
    return value


def run(report):
    url = make_url(os.environ.get("SCC_DATABASE_URL", ""))
    require(
        os.environ.get("SCC_COLLECTION_TEST_PRIVATE_CLUSTER") == "a32-private-cluster"
        and os.environ.get("SCC_COLLECTION_TEST_DATABASE_URL")
        == url.render_as_string(hide_password=False)
        and url.host == "127.0.0.1"
        and url.database == "scc_a32_tests"
        and url.username == "scc_test"
        and url.port not in {None, 5432},
        "Use the owned private PostgreSQL runner",
    )
    require(
        not any(bench.owned(n) for n in bench.read_state()["nodes"]),
        "Existing native bench is running",
    )
    database = Database(Settings())
    database.check_ready()
    with database.session() as session:
        require(
            session.scalar(select(func.count()).select_from(ManagedNode)) == 0,
            "Live check requires an empty owned database",
        )
    password, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    admin = create_administrator(database, "admin", password)
    with database.session() as session:
        password_hash = session.get(AppUser, admin).password_hash
    env = {
        **os.environ,
        "SCC_SESSION_SECRET": secret,
        "PYTHONPATH": str(ROOT / "backend"),
        "PYTHONIOENCODING": "utf-8",
    }
    bench.RUNTIME.mkdir(parents=True, exist_ok=True)
    api = Api(env)
    api.log = bench.RUNTIME / "a35-api.log"
    started, control = False, None
    created_dirs = []

    def passed(name, **facts):
        report["checks"].append({"name": name, "at": bench.utc_now(), **facts})
        print(f"PASS: {name}", flush=True)

    try:
        started = True
        state = bench.start()
        nodes = state["nodes"]
        probes = [bench.BenchClient(node) for node in nodes]
        initial_configs = [probe.get("/rest/config/folders") for probe in probes]
        connections = {
            f"demo-{letter}": {
                "display_name": f"Demo {letter}",
                "base_url": f"http://127.0.0.1:{node['api_port']}",
                "api_key": probe.key,
            }
            for letter, node, probe in zip("ab", nodes, probes, strict=True)
        }
        env["SCC_CONNECTIONS"] = json.dumps(connections)
        api.env = env
        api.forbidden = [
            password,
            password_hash,
            secret,
            url.password,
            *[p.key for p in probes],
            *[n["home"] for n in nodes],
            *[c["base_url"] for c in connections.values()],
        ]
        control = HttpSyncthingAdapter(
            NodeConnection(
                node_id=uuid4(), base_url=connections["demo-a"]["base_url"], api_key=probes[0].key
            ),
            expected_device_id=nodes[0]["device_id"],
        )

        # Fill the native 1000-event default buffer with real transferred tiny files.
        # Register afterwards: the current anchor proves the epoch, while DB cursor=0.
        fixture = "a35-" + uuid4().hex
        for node in nodes:
            parent = (Path(node["home"]) / "data" / bench.FOLDERS[1]).resolve()
            require(parent.is_relative_to(bench.RUNTIME.resolve()), "Fixture outside owned runtime")
            target = parent / fixture
            require(not target.exists(), "Fixture directory already exists")
            created_dirs.append(target)
        created_dirs[0].mkdir()
        for index in range(600):
            (created_dirs[0] / f"file-{index:04d}.txt").write_bytes(b"A3 public test fixture\n")
        probes[0].request(f"/rest/db/scan?folder={bench.FOLDERS[1]}", method="POST")

        def transferred():
            return created_dirs[1].is_dir() and len(list(created_dirs[1].glob("file-*.txt"))) == 600

        wait_for(transferred, 90)
        wait_for(lambda: probes[1].get("/rest/events?since=0&timeout=0")[0]["id"] > 1, 20)
        window = probes[1].get("/rest/events?since=0&timeout=0")
        passed(
            "native_buffer_rollover",
            files=600,
            first=window[0]["id"],
            last=window[-1]["id"],
            retained=len(window),
        )

        api.start(connections)
        api.request("GET", "/api/events", expected=401)
        api.request("GET", "/api/snapshots?scope=node", expected=401)
        headers = api.login(password)
        ids = [
            api.request(
                "POST",
                "/api/nodes",
                {"connection_ref": ref, "display_name": ref},
                headers=headers,
                expected=201,
                model=dto.Node,
            )[1]["id"]
            for ref in connections
        ]
        before = [checkpoint(database, node_id) for node_id in ids]
        folders = [folder_state(database, identifier) for identifier in ids]
        require(all(len(value) == 2 for value in folders), "Incomplete initial folder mappings")

        # An actual native batch is committed twice from the same read point.
        store = EventStore(Settings(connections=connections), database, SystemClock())
        point = store.load(UUID(ids[1]))
        client = HttpSyncthingAdapter(
            point.target.connection,
            checkpoint=point.target.checkpoint,
            expected_device_id=point.target.expected_device_id,
        )
        try:
            batch = client.read_events(point.cursor, timeout_seconds=0)
        finally:
            client.close()
        first, repeat = store.save(point, batch), store.save(point, batch)
        require(
            first.outcome == "stored"
            and first.markers_inserted == 1
            and repeat.outcome == "conflict"
            and repeat.events_inserted == 0,
            "Repeated native batch was not atomic/idempotent",
        )
        with database.session() as session:
            gap = session.scalar(
                select(SyncEvent).where(SyncEvent.node_id == UUID(ids[1]), SyncEvent.kind == "gap")
            )
            require(
                gap is not None
                and gap.payload_summary == {"from_id": 1, "to_id": batch.events[0].id - 1},
                "Native bounded gap differs from window",
            )
        passed(
            "T07_native_batch_replay_and_T08_bounded_gap",
            received=len(batch.events),
            gap_from=1,
            gap_to=batch.events[0].id - 1,
        )

        control.set_paused(bench.FOLDERS[0], True)
        wait_for(lambda: control.read_folder(bench.FOLDERS[0]).paused is True)
        results = cycle(env, ids)
        require(
            all(
                r["events"]["outcome"] == "stored" and r["observations"]["outcome"] == "stored"
                for r in results
            ),
            "Initial native collection failed",
        )
        require(
            all(value == 1 for value in node_counts(database).values()),
            "Folder observations inflated the node denominator",
        )
        with database.session() as session:
            node = session.get(ManagedNode, UUID(ids[0]))
            snapshots = session.scalars(
                select(StatusSnapshot).where(
                    StatusSnapshot.node_id == node.id, StatusSnapshot.scope == "folder"
                )
            ).all()
            require(
                any(row.success and row.actual_paused is True for row in snapshots)
                and any(
                    not row.success
                    and row.error_code.startswith("runtime_")
                    and row.actual_paused is None
                    and row.runtime_state is None
                    and row.need_bytes is None
                    and row.pull_errors is None
                    for row in snapshots
                ),
                "Paused runtime/config results were mixed",
            )
        control.set_paused(bench.FOLDERS[0], False)
        wait_for(lambda: control.read_folder(bench.FOLDERS[0]).paused is False)
        cycle(env, [ids[0]])
        require(
            folder_state(database, ids[0])[bench.FOLDERS[0]][4] is not None,
            "Resumed native runtime did not recover",
        )
        passed("real_login_guard_config_runtime_and_one_node_probe_per_cycle")

        # Each cycle is a newly launched backend process, resolving only DB proof.
        proof = [checkpoint(database, identifier) for identifier in ids]
        cycle(env, ids)
        require(
            all(
                checkpoint(database, identifier)[2] == old[2]
                and checkpoint(database, identifier)[4] >= old[4]
                for identifier, old in zip(ids, proof, strict=True)
            ),
            "Fresh worker did not resume persisted proof",
        )
        api.stop()
        api.start(connections)
        api.request("GET", "/api/events?limit=1", model=dto.EventPage)
        counts = node_counts(database)
        require(
            cycle(env) == [] and node_counts(database) == counts,
            "Not-due pass fabricated measurements",
        )
        passed("backend_restart_retains_session_cursor_anchor_and_due_semantics")

        previous_folders = folder_state(database, ids[0])
        previous_proof = checkpoint(database, ids[0])
        bench.stop({**state, "nodes": [state["nodes"][0]]})
        bench.write_json(bench.STATE, state)
        results = cycle(env, ids)
        require(
            results[0]["events"]["outcome"] == "error"
            and results[0]["observations"]["node_error"] == "unreachable"
            and results[1]["observations"]["node_error"] is None,
            "Native outage was not recorded independently per node",
        )
        require(
            checkpoint(database, ids[0]) == previous_proof
            and folder_state(database, ids[0]) == previous_folders,
            "Outage changed proof or replaced cached measurements",
        )
        require(
            all(node_counts(database)[key] == value + 1 for key, value in counts.items()),
            "Outage denominator includes unperformed checks",
        )
        # API history is DB-only and still serves an unavailable source.
        api.request("GET", f"/api/snapshots?node_id={ids[0]}&scope=node", model=dto.SnapshotPage)
        passed("T05_native_outage_stale_cache_and_available_history")

        bench.stop()
        wait_for(ports_free, 130)
        state = bench.start()
        results = cycle(env, ids)
        require(
            all(
                r["events"]["outcome"] == "stored" and r["observations"]["node_error"] is None
                for r in results
            ),
            "Native source recovery failed",
        )
        with database.session() as session:
            for identifier, old, old_folders in zip(ids, before, folders, strict=True):
                row = session.get(ManagedNode, UUID(identifier))
                require(
                    row.source_start_time_raw != old[1] and row.source_session_id != old[2],
                    "Real source restart did not create a new epoch",
                )
                marker = session.scalar(
                    select(SyncEvent).where(
                        SyncEvent.node_id == row.id, SyncEvent.event_type == "SourceRestarted"
                    )
                )
                require(
                    marker is not None
                    and marker.source_event_id is None
                    and marker.payload_summary["previous_session_id"] == old[2],
                    "Missing restart boundary",
                )
                require(
                    {key: value[0] for key, value in folder_state(database, identifier).items()}
                    == {key: value[0] for key, value in old_folders.items()},
                    "Source restart changed folder UUIDs",
                )
        passed("T08_real_restart_boundary_and_T05_current_reconciliation")

        # Deliberate legacy DB condition, with real unchanged source and real adapter.
        old = checkpoint(database, ids[0])
        with database.session() as session:
            session.get(ManagedNode, UUID(ids[0])).source_stream_anchor = None
            session.commit()
        result = cycle(env, [ids[0]])[0]
        with database.session() as session:
            marker = session.scalar(
                select(SyncEvent).where(
                    SyncEvent.node_id == UUID(ids[0]),
                    SyncEvent.event_type == "SourceContinuityLost",
                )
            )
            require(
                result["events"]["outcome"] == "stored"
                and marker is not None
                and marker.payload_summary["previous_session_id"] == old[2]
                and checkpoint(database, ids[0])[1] == old[1]
                and checkpoint(database, ids[0])[2] != old[2],
                "Legacy continuity loss was hidden",
            )
        passed("T08_legacy_missing_proof_same_native_start_time")

        page = api.request("GET", "/api/events?limit=2", model=dto.EventPage)[1]
        second = api.request("GET", "/api/events?limit=2&offset=2", model=dto.EventPage)[1]
        require(
            page["total"] == second["total"]
            and len(page["items"]) == 2
            and not ({v["id"] for v in page["items"]} & {v["id"] for v in second["items"]}),
            "Real HTTP pagination is inconsistent",
        )
        a_folder = str(next(iter(folder_state(database, ids[0]).values()))[0])
        require(
            api.request("GET", f"/api/events?node_id={ids[1]}&folder_id={a_folder}")[1]["total"]
            == 0,
            "Node/folder AND filter crossed node mappings",
        )
        require(
            api.request("GET", f"/api/snapshots?scope=node&folder_id={a_folder}")[1]["total"] == 0,
            "Scope filter returned folder rows as node probes",
        )
        api.request(
            "GET", "/api/events?from=2026-01-01T00:00:00Z&to=2026-01-01T00:00:00Z", expected=422
        )
        api.request("GET", "/api/snapshots?scope=node", model=dto.SnapshotPage)
        with database.session() as session:
            duplicate = session.execute(
                select(
                    SyncEvent.node_id,
                    SyncEvent.source_session_id,
                    SyncEvent.source_event_id,
                    func.count(),
                )
                .where(SyncEvent.kind == "source")
                .group_by(SyncEvent.node_id, SyncEvent.source_session_id, SyncEvent.source_event_id)
                .having(func.count() > 1)
            ).first()
            require(duplicate is None, "Duplicate source positions in real database")
            payloads = session.scalars(select(SyncEvent.payload_summary)).all()
            require(
                all(
                    set(value) <= {"from_id", "to_id", "state", "previous_session_id"}
                    for value in payloads
                ),
                "Unapproved source payload",
            )
            serialized = json.dumps(payloads).encode()
            require(
                not any(value.encode() in serialized for value in api.forbidden),
                "Private value persisted in event payload",
            )
            report["persisted"] = {
                "events": session.scalar(select(func.count()).select_from(SyncEvent)),
                "snapshots": session.scalar(select(func.count()).select_from(StatusSnapshot)),
                "node_probes": sum(node_counts(database).values()),
                "duplicate_source_positions": 0,
            }
        passed("guarded_history_pagination_filters_safe_payloads_and_source_uniqueness")
        require(
            [bench.BenchClient(node).get("/rest/config/folders") for node in state["nodes"]]
            == initial_configs,
            "Demo config was not restored",
        )
        passed("full_demo_folder_configuration_restored")
        report["result"] = "passed"
    finally:
        api.stop()
        if control is not None:
            try:
                if control.read_folder(bench.FOLDERS[0]).paused:
                    control.set_paused(bench.FOLDERS[0], False)
            except AdapterError:
                pass
            control.close()
        cleanup = bench.stop() if started else {"running": []}
        database.close()
        # Delete only this run's explicitly named fixtures, after native processes stopped.
        for directory in created_dirs:
            require(
                directory.resolve().is_relative_to(bench.RUNTIME.resolve()),
                "Fixture cleanup outside owned runtime",
            )
            if directory.exists():
                for path in directory.iterdir():
                    require(
                        path.is_file() and path.name.startswith("file-") and path.suffix == ".txt",
                        "Unexpected fixture content retained",
                    )
                    path.unlink()
                directory.rmdir()
        report["cleanup"] = {
            "api_running": api.process is not None,
            "syncthing_running": cleanup["running"],
            "fixture_directories_present": any(p.exists() for p in created_dirs),
        }
        private = [*api.forbidden, *api.tokens]
        logs = api.log.read_bytes() if api.log.exists() else b""
        require(not any(value.encode() in logs for value in private), "Private API log value")
        require(
            not any(value.encode() in json.dumps(report).encode() for value in private),
            "Private report value",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "stage": "A3.5",
        "started_at": bench.utc_now(),
        "result": "failed",
        "syncthing_version": bench.release()["version"],
        "checks": [],
    }
    try:
        with bench.operation_lock():
            run(report)
    except Exception as error:
        report["result"] = "failed"
        report["error_type"] = type(error).__name__
        if isinstance(error, bench.BenchError):
            report["error"] = str(error)
        print(
            "Live collection check failed; inspect the current scenario and private runtime",
            flush=True,
        )
    finally:
        report["completed_at"] = bench.utc_now()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
