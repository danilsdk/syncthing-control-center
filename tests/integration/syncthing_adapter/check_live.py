"""Explicit A1.2–A1.4 real test; owns only the disposable deploy/syncthing bench."""

import argparse
import hashlib
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "deploy/syncthing"))

import bench  # noqa: E402
from app.contracts.interfaces import AdapterError, NodeConnection  # noqa: E402
from app.syncthing_adapter import HttpAdapterFactory, HttpSyncthingAdapter, ResolvedNode  # noqa: E402
from capture import assert_public  # noqa: E402

SHARED, CONTROL = bench.FOLDERS


def require(condition, message):
    if not condition:
        raise bench.BenchError(message)


def wait_for(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise bench.BenchError(message)


def unchanged_except_paused(before, after, desired):
    expected = deepcopy(before)
    expected["paused"] = desired
    changed = sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )
    require(
        after == expected, "PATCH changed non-target configuration fields: " + ",".join(changed)
    )
    return changed


def observe(client, desired, expected_session):
    wait_for(
        lambda: client.read_folder(SHARED).paused is desired,
        10,
        "Paused configuration was not observed before its deadline",
    )
    requires_restart = client.restart_required()
    system = client.read_system()
    require(not requires_restart, "Paused change requires a restart")
    require(
        system.source_session_id == expected_session, "Source session changed during observation"
    )
    try:
        runtime = client.read_runtime(SHARED).model_dump(mode="json")
    except AdapterError as error:
        # A paused source may return state=""; A0 cannot represent this as a known state.
        if error.code not in {"invalid_response", "not_found"}:
            raise
        runtime = {"available": False, "error_code": error.code.value}
    return {
        "config_paused": desired,
        "requires_restart": requires_restart,
        "source_session_id": system.source_session_id,
        "observed_at": bench.utc_now(),
        "runtime": runtime,
    }


def run(export):
    require(
        not any(bench.owned(node) for node in bench.read_state()["nodes"]),
        "Stop the existing bench before this lifecycle-owned check",
    )
    report = {
        "stage": "A1.2-A1.4",
        "started_at": bench.utc_now(),
        "version": bench.release()["version"],
        "result": "failed",
        "automatic_restart_during_paused": False,
        "same_second_restart_limitation": True,
        "human_review": "pending before merge",
    }
    factory = None
    forbidden = [str(ROOT.resolve()), str(ROOT.resolve()).replace("\\", "/")]
    stopped = []
    try:
        state = bench.start()
        require(len(state["nodes"]) == 2, "The disposable bench must have two nodes")
        targets = {}
        for index, node in enumerate(state["nodes"], 1):
            key = bench.BenchClient(node).key
            forbidden.extend([key, node["home"], node["home"].replace("\\", "/")])
            targets[UUID(int=index)] = ResolvedNode(
                NodeConnection(
                    node_id=UUID(int=index),
                    base_url=f"http://127.0.0.1:{node['api_port']}",
                    api_key=key,
                ),
                expected_device_id=node["device_id"],
            )
        factory = HttpAdapterFactory(targets.__getitem__)
        first, second = factory.for_node(UUID(int=1)), factory.for_node(UUID(int=2))
        raw_first, raw_second = (bench.BenchClient(node) for node in state["nodes"])
        before_system = first.read_system()
        report["nodes"] = []
        for client in (first, second):
            wait_for(
                lambda client=client: client.read_runtime(SHARED).state == "idle",
                15,
                "Initial folder did not become idle",
            )
            folders = client.list_folders()
            require(
                {folder.syncthing_folder_id for folder in folders} == set(bench.FOLDERS),
                "Initial folder list does not match the disposable bench",
            )
            report["nodes"].append(
                {
                    "system": client.read_system().model_dump(mode="json"),
                    "folders": [item.model_dump(mode="json") for item in folders],
                    "runtime": client.read_runtime(SHARED).model_dump(mode="json"),
                    "restart_required": client.restart_required(),
                }
            )

        batch = first.read_events(0, 0)
        require(bool(batch.events), "The source event buffer was unexpectedly empty")
        report["events"] = {"initial_batch": batch.model_dump(mode="json")}
        cursor = batch.events[-1].id
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            current = first.read_events(cursor, 1)
            if not current.events:
                report["events"].update(empty_batch_observed=True, cursor=cursor)
                break
            cursor = current.events[-1].id
        else:
            raise bench.BenchError("No quiet long-poll interval was observed")

        baseline = raw_first.get(f"/rest/config/folders/{SHARED}")
        controls = [
            raw_first.get(f"/rest/config/folders/{CONTROL}"),
            raw_second.get(f"/rest/config/folders/{SHARED}"),
            raw_second.get(f"/rest/config/folders/{CONTROL}"),
        ]
        session = first.read_system().source_session_id
        require(
            session == before_system.source_session_id, "Source restarted during baseline reads"
        )
        first.set_paused(SHARED, True)
        paused = observe(first, True, session)
        paused["source_runtime"] = bench.sanitize(
            "folder-runtime", raw_first.get(f"/rest/db/status?folder={SHARED}")
        )
        paused["changed_config_fields"] = unchanged_except_paused(
            baseline, raw_first.get(f"/rest/config/folders/{SHARED}"), True
        )
        report["pause"] = paused

        probe_name = f"a14-probe-{uuid4()}.txt"
        payload = f"SCC A1.4 disposable probe {uuid4()}\n".encode()
        sender = Path(state["nodes"][1]["home"]) / "data" / SHARED / probe_name
        receiver = Path(state["nodes"][0]["home"]) / "data" / SHARED / probe_name
        sender.write_bytes(payload)
        scan = raw_second.request(f"/rest/db/scan?folder={SHARED}", method="POST")
        require(scan["status_code"] == 200, "Sender scan was not accepted")
        checks = 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            require(first.read_folder(SHARED).paused, "The target unexpectedly left paused state")
            require(not receiver.exists(), "The disposable file arrived while target was paused")
            checks += 1
            time.sleep(0.15)
        report["pause"].update(file_absent_during_observation=True, absence_checks=checks)

        first.set_paused(SHARED, False)
        resumed = observe(first, False, session)
        resumed["changed_config_fields"] = unchanged_except_paused(
            baseline, raw_first.get(f"/rest/config/folders/{SHARED}"), False
        )
        report["resume"] = resumed
        wait_for(
            lambda: receiver.exists() and receiver.read_bytes() == payload,
            35,
            "The disposable file did not synchronize after resume",
        )
        report["transfer_after_resume"] = {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "exact_content_matched": True,
        }
        require(
            controls
            == [
                raw_first.get(f"/rest/config/folders/{CONTROL}"),
                raw_second.get(f"/rest/config/folders/{SHARED}"),
                raw_second.get(f"/rest/config/folders/{CONTROL}"),
            ],
            "A control folder or a folder on the other node was changed",
        )
        report["non_target_folders_unchanged"] = True
        report["issue_10389_not_reproduced"] = True

        target = targets[UUID(int=1)]
        wrong = HttpSyncthingAdapter(
            target.connection.model_copy(
                update={"api_key": type(target.connection.api_key)("scc-a1-deliberately-wrong")}
            ),
            expected_device_id=target.expected_device_id,
        )
        try:
            wrong.read_system()
        except AdapterError as error:
            require(error.code == "auth_error", "Wrong key was not classified as auth_error")
            report["wrong_key"] = error.code.value
        else:
            raise bench.BenchError("Wrong key unexpectedly succeeded")
        finally:
            wrong.close()
        for operation in ("read", "write"):
            try:
                if operation == "read":
                    first.read_folder("scc-a1-missing")
                else:
                    first.set_paused("scc-a1-missing", True)
            except AdapterError as error:
                require(error.code == "not_found", "Missing folder did not produce not_found")
                if operation == "write":
                    require(
                        error.write_outcome == "not_applied", "404 write outcome was not definite"
                    )
                report[f"missing_folder_{operation}"] = {
                    "code": error.code.value,
                    "write_outcome": error.write_outcome,
                }
            else:
                raise bench.BenchError("Missing folder operation unexpectedly succeeded")

        old = first.read_system()
        stopped.extend(bench.stop()["stopped"])
        bench.start()  # Explicit lifecycle test, unrelated to applying paused.
        new = first.read_system()
        require(new.device_id == old.device_id, "Disposable source identity changed across restart")
        require(
            new.source_session_id != old.source_session_id,
            "Normal restart did not change the session",
        )
        restarted_batch = first.read_events(0, 0)
        require(
            bool(restarted_batch.events) and restarted_batch.events[0].id == 1,
            "Restarted source stream did not start at its initial position",
        )
        report["explicit_restart_test"] = {
            "old_session": old.source_session_id,
            "new_session": new.source_session_id,
            "identity_preserved": True,
            "first_source_event_id": restarted_batch.events[0].id,
        }
        stopped.extend(bench.stop()["stopped"])
        try:
            first.read_system()
        except AdapterError as error:
            require(
                error.code in {"unreachable", "timeout"},
                "Stopped source returned an unexpected error",
            )
            report["stopped_source"] = error.code.value
        else:
            raise bench.BenchError("Stopped source unexpectedly responded")
        report["result"] = "passed"
    except AdapterError as error:
        report["error"] = error.code.value
        raise
    except bench.BenchError as error:
        report["error"] = str(error)
        raise
    finally:
        if factory is not None:
            factory.close()
        cleanup = bench.stop()
        stopped.extend(cleanup["stopped"])
        report["cleanup"] = {"stopped": sorted(set(stopped)), "running": cleanup["running"]}
        report["completed_at"] = bench.utc_now()
        bench.write_json(bench.RUNTIME / "adapter-verification.json", report)
        if export and report["result"] == "passed":
            path = Path(__file__).with_name("evidence-adapter-v2.1.7.json")
            path.write_text(assert_public(report, forbidden), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-evidence", action="store_true")
    args = parser.parse_args()
    try:
        with bench.operation_lock():
            result = run(args.export_evidence)
        print(
            json.dumps(
                {key: value for key, value in result.items() if key not in {"nodes", "events"}},
                indent=2,
            )
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "failed",
                    "error": str(error)
                    if isinstance(error, bench.BenchError)
                    else "See the private runtime report",
                    "error_type": type(error).__name__,
                }
            )
        )
        raise SystemExit(1) from None
