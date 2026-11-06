"""Real source checkpoints and an explicit simulation of coarse-time collisions."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import check_live

bench = check_live.bench
ROOT = check_live.ROOT

from app.contracts.interfaces import AdapterError, NodeConnection, SourceCheckpoint  # noqa: E402
from app.syncthing_adapter import HttpSyncthingAdapter  # noqa: E402
from app.syncthing_adapter.transport import HttpTransport, Response  # noqa: E402


def target():
    state = bench.read_state()
    check_live.require(
        len(state["nodes"]) == 2 and all(bench.owned(n) for n in state["nodes"]),
        "Both owned demo processes must be running",
    )
    node = state["nodes"][0]
    raw = bench.BenchClient(node)
    connection = NodeConnection(
        node_id=UUID(int=1), base_url=f"http://127.0.0.1:{node['api_port']}", api_key=raw.key
    )
    return state, node, raw, connection


def resume_child():
    _state, node, _raw, connection = target()
    saved = json.loads((bench.RUNTIME / "d01-checkpoint.json").read_text(encoding="utf-8"))
    checkpoint = SourceCheckpoint.model_validate(saved["checkpoint"])
    client = HttpSyncthingAdapter(
        connection, expected_device_id=node["device_id"], checkpoint=checkpoint
    )
    try:
        batch = client.read_events(saved["cursor"], 0)
        print(
            json.dumps(
                {
                    "source_session_id": batch.source_session_id,
                    "checkpoint": batch.source_checkpoint.model_dump(mode="json"),
                }
            )
        )
    finally:
        client.close()


class FrozenStatusTransport:
    """Only the test view of coarse status fields changes; event bodies remain real."""

    def __init__(self, connection, start_time):
        self.inner = HttpTransport(connection)
        self.start_time = start_time
        self.actual_starts = set()

    def request(self, method, path, **kwargs):
        response = self.inner.request(method, path, **kwargs)
        if method == "GET" and path == "/rest/system/status":
            value = json.loads(response.body)
            self.actual_starts.add(value["startTime"])
            value["startTime"] = self.start_time
            value["uptime"] = 1000000  # The test cannot pass merely by noticing uptime regression.
            return Response(json.dumps(value).encode(), response.content_type, response.device_id)
        return response

    def close(self):
        self.inner.close()


def run():
    report = {
        "stage": "D-01-r2",
        "started_at": bench.utc_now(),
        "result": "failed",
        "scheme": "scc:source-epoch:v2",
    }
    clients, stopped, forbidden = [], [], [str(ROOT), str(ROOT).replace("\\", "/")]
    try:
        regression = check_live.run(False)
        report["adapter_regression"] = {
            key: regression[key]
            for key in (
                "result",
                "pause",
                "resume",
                "transfer_after_resume",
                "non_target_folders_unchanged",
                "explicit_restart_test",
            )
        }
        check_live.start_after_owned_stop()
        state, node, raw, connection = target()
        for item in state["nodes"]:
            forbidden.extend(
                [bench.BenchClient(item).key, item["home"], item["home"].replace("\\", "/")]
            )
        client = HttpSyncthingAdapter(connection, expected_device_id=node["device_id"])
        clients.append(client)
        batch = client.read_events(0, 0)
        check_live.require(bool(batch.events), "Initial source stream is empty")
        bench.write_json(
            bench.RUNTIME / "d01-checkpoint.json",
            {
                "checkpoint": batch.source_checkpoint.model_dump(mode="json"),
                "cursor": batch.events[-1].id,
            },
        )
        client.close()
        identities = [item["process"] for item in state["nodes"]]
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--resume-child"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=35,
            creationflags=bench.subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        check_live.require(child.returncode == 0, "Restored backend client failed")
        restored = json.loads(child.stdout)
        check_live.require(
            restored["source_session_id"] == batch.source_session_id,
            "Backend recreation incorrectly changed a proven source epoch",
        )
        check_live.require(
            all(bench.process_identity(item["pid"]) == item for item in identities),
            "Demo process unexpectedly changed during backend recreation",
        )
        checkpoint = SourceCheckpoint.model_validate(restored["checkpoint"])
        report["backend_process_recreation"] = {
            "epoch_preserved": True,
            "native_processes_unchanged": True,
            "checkpoint": checkpoint.model_dump(mode="json"),
        }

        transport = FrozenStatusTransport(connection, checkpoint.start_time_raw)
        frozen = HttpSyncthingAdapter(
            connection,
            expected_device_id=node["device_id"],
            checkpoint=checkpoint,
            transport=transport,
        )
        clients.append(frozen)
        before = frozen.read_system()
        stopped.extend(bench.stop()["stopped"])
        check_live.start_after_owned_stop()
        _state, _node, raw, _connection = target()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            events = raw.get("/rest/events?since=0&timeout=0")
            if events and events[-1]["id"] > before.stream_anchor.id:
                break
            raw.request("/rest/db/scan?folder=scc-a1-control", method="POST")
            time.sleep(0.05)
        else:
            raise bench.BenchError("Restarted source did not overtake the old checkpoint")
        try:
            frozen.read_events(before.stream_anchor.id, 0)
        except AdapterError as error:
            check_live.require(
                error.code == "session_changed", "Old cursor produced an unexpected result"
            )
        else:
            raise bench.BenchError("Coarse-time collision silently accepted an old cursor")
        new_batch = frozen.read_events(0, 0)
        check_live.require(
            new_batch.source_session_id != before.source_session_id and new_batch.events[0].id == 1,
            "Collision recovery did not create a distinct epoch from the stream beginning",
        )
        check_live.require(
            len(transport.actual_starts) == 2, "A real native restart was not observed"
        )
        report["coarse_time_collision"] = {
            "mode": "test transport freezes startTime and uptime; real source events and restart",
            "native_same_second_restart_claimed": False,
            "actual_native_start_times": sorted(transport.actual_starts),
            "status_start_time_before_after": before.start_time_raw,
            "old_anchor_id": before.stream_anchor.id,
            "new_tail_before_read": events[-1]["id"],
            "old_cursor_rejected": True,
            "old_epoch": before.source_session_id,
            "new_epoch": new_batch.source_session_id,
            "first_rebased_event_id": new_batch.events[0].id,
        }
        report["result"] = "passed"
    finally:
        for client in clients:
            client.close()
        cleanup = bench.stop()
        stopped.extend(cleanup["stopped"])
        report["cleanup"] = {"stopped": sorted(set(stopped)), "running": cleanup["running"]}
        report["completed_at"] = bench.utc_now()
        bench.write_json(bench.RUNTIME / "d01-verification.json", report)
        if report["result"] == "passed":
            Path(__file__).with_name("evidence-d01-v2.1.7.json").write_text(
                check_live.assert_public(report, forbidden), encoding="utf-8"
            )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.resume_child:
            resume_child()
        else:
            with bench.operation_lock():
                result = run()
            print(
                json.dumps({k: v for k, v in result.items() if k != "adapter_regression"}, indent=2)
            )
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "failed",
                    "error": str(error)
                    if isinstance(error, bench.BenchError)
                    else "D-01 live verification failed",
                    "error_type": type(error).__name__,
                }
            )
        )
        raise SystemExit(1) from None
