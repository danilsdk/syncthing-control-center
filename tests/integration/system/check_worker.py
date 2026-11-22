"""A4.3 real Compose/API/worker tests, including loss within a real A3 source read."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from check_transfer import CONTROL, SHARED, Fixture, check_config, require, wait_for

PROBE = Path(__file__).with_name("worker_probe.py").read_text(encoding="utf-8")


def window(paused):
    now = datetime.now(UTC)
    minute = now.hour * 60 + now.minute

    def hhmm(offset):
        value = (minute + offset) % 1440
        return f"{value // 60:02d}:{value % 60:02d}"

    return {
        "timezone": "UTC",
        "days_of_week": list(range(1, 8)),
        "start_time": hhmm(30 if paused else -30),
        "end_time": hhmm(60 if paused else 30),
    }


def verify_worker(stack, api, ids, headers, passed, report):
    stack.assert_owned()
    report["phase"] = "A43_worker_setup"
    fixture, nonce = Fixture(stack, ids), uuid4().hex

    def probe(mode, current=nonce, **values):
        result = stack.compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            PROBE,
            input=json.dumps({"nonce": current, "mode": mode, **values}).encode(),
            timeout=35,
        )
        return json.loads(result.stdout)

    def launch_probe(mode, current, **values):
        path = stack.directory / ("worker-" + current + ".log")
        with path.open("wb") as output:
            process = subprocess.Popen(
                [*stack.docker, *stack.compose_args, "exec", "-T", "api", "python", "-c", PROBE],
                cwd=stack.directory,
                env=stack.env,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        process.stdin.write(json.dumps({"nonce": current, "mode": mode, **values}).encode())
        process.stdin.close()
        return process

    probe("create")
    original = fixture.probe("snapshot")
    target = next(
        row["id"]
        for row in api.request("GET", f"/api/nodes/{ids[0]}/folders")["items"]
        if row["syncthing_folder_id"] == SHARED
    )
    unsigned = type(api)(stack.state["port"], api.forbidden)
    for endpoint in ("events", "snapshots", "rules", "executions"):
        unsigned.request("GET", "/api/" + endpoint, expected=401)
    api.request("POST", "/api/rules", {}, headers={"Origin": api.origin}, expected=403)
    rule = api.request(
        "POST",
        "/api/rules",
        {"folder_id": target, "enabled": True, **window(True)},
        headers=headers,
        expected=201,
    )

    def patch(**fields):
        return api.request(
            "PATCH",
            "/api/rules/" + rule["id"],
            {"expected_version": rule["version"], **fields},
            headers=headers,
        )

    def applied(paused):
        rows = api.request("GET", f"/api/executions?folder_id={target}&source=rule&status=applied")[
            "items"
        ]
        return any(
            row["rule_version"] == rule["version"]
            and row["desired_paused"] is paused
            and row["observed_paused"] is paused
            and row["verified_at"] is not None
            for row in rows
        )

    def state(paused):
        return fixture.probe("state", folder=SHARED)["paused"] is paused

    def start():
        stack.compose("up", "-d", "--wait", "--wait-timeout", "30", "worker", timeout=45)
        wait_for(lambda: probe("state")["workers"] == 1, 35, "Worker did not acquire singleton")

    def stop():
        stack.compose("stop", "--timeout", "30", "worker", timeout=40)
        wait_for(lambda: probe("state")["workers"] == 0, 15, "Stopped worker retained singleton")

    projection = next(
        row
        for row in api.request("GET", f"/api/nodes/{ids[0]}/folders")["items"]
        if row["id"] == target
    )
    require(projection["desired_paused"] is True, "Registry did not use B1's desired state")

    report["phase"] = "A4_folder_lock_contention"
    holder = launch_probe("hold-folder", nonce, folder_id=target)
    try:

        def folder_locked():
            require(holder.poll() is None, "Owned folder lock holder exited early")
            return probe("control")["ready"]

        wait_for(folder_locked, 20, "Could not hold the real demo folder lock")
        start()
        worker_pids = probe("state")["worker_pids"]
        require(len(worker_pids) == 1, "Expected one worker during folder lock contention")

        def deferred():
            require(probe("state")["worker_pids"] == worker_pids, "Busy folder stopped the worker")
            logs = stack.compose("logs", "--no-color", "--tail", "50", "worker", timeout=10)
            return b"Reconciliation deferred: a folder lock is busy" in logs.stdout + logs.stderr

        wait_for(deferred, 25, "Worker did not defer the busy folder pass")
        snapshots = probe("state")["counts"]["status_snapshots"]

        def collected_again():
            health = probe("state")
            require(
                health["worker_pids"] == worker_pids, "Worker lost its singleton during contention"
            )
            return health["counts"]["status_snapshots"] > snapshots

        wait_for(collected_again, 25, "Collection stopped while the folder lock was held")
        require(holder.poll() is None and state(False), "Worker changed the locked demo folder")
        require(
            api.request("GET", f"/api/executions?folder_id={target}")["total"] == 0,
            "Worker recorded a command before acquiring the folder lock",
        )
    finally:
        if holder.poll() is None:
            probe("control", action="gate-release")
        require(holder.wait(timeout=20) == 0, "Owned folder lock holder failed")
    wait_for(lambda: applied(True), 45, "The active rule was not applied after releasing the lock")
    require(probe("state")["worker_pids"] == worker_pids, "Worker was restarted during recovery")
    passed("A4_busy_folder_preserves_singleton_collection_and_retries")

    report["phase"] = "A43_auth_desired_and_rule_pause"
    require(state(True), "Applied rule did not pause its source folder")
    check_config(original, fixture.probe("snapshot"), True)
    passed("A43_protected_routes_B1_projection_and_real_rule_pause")

    second = stack.compose(
        "exec",
        "-T",
        "api",
        "python",
        "/opt/deploy/entrypoint.py",
        "worker",
        check=False,
        timeout=30,
    )
    require(
        second.returncode == 3 and probe("state")["workers"] == 1,
        "Second worker was not rejected by the real PostgreSQL singleton",
    )
    passed("A43_second_worker_rejected_before_runtime", exit_code=3)

    report["phase"] = "A43_rule_transfer"
    pending, control = fixture.create(SHARED), fixture.create(CONTROL)
    wait_for(
        lambda: fixture.file("a", CONTROL, control["file_name"]) == control["sha256"],
        45,
        "Control transfer did not confirm live source connectivity",
    )
    for _ in range(3):
        require(
            state(True) and fixture.file("a", SHARED, pending["file_name"]) is None,
            "File arrived while the rule's target was paused",
        )
    rule = patch(**window(False))
    wait_for(lambda: applied(False), 45, "Rule resume was not observed and confirmed")
    wait_for(
        lambda: fixture.file("a", SHARED, pending["file_name"]) == pending["sha256"],
        45,
        "Rule resume did not deliver exact file content",
    )
    check_config(original, fixture.probe("snapshot"), False)
    report["worker_transfer"] = {
        "sha256": pending["sha256"],
        "bytes": pending["bytes"],
        "control_transferred_during_pause": True,
    }
    passed("A43_rule_pause_positive_control_resume_and_sha256_transfer")

    rule = patch(enabled=False)
    fixture.probe("pause", folder=SHARED, paused=True)
    execution = api.request(
        "POST",
        "/api/executions/resume",
        {"folder_id": target, "idempotency_key": str(uuid4())},
        headers=headers,
        expected=202,
    )
    wait_for(
        lambda: api.request("GET", "/api/executions/" + execution["id"])["status"] == "applied",
        45,
        "Manual resume was not confirmed by the shared worker",
    )
    require(state(False), "Manual applied result differs from the source")
    passed("A43_manual_resume_after_disabled_rule_is_observed")

    stop()
    rule = patch(enabled=True, **window(True))
    start()
    wait_for(lambda: applied(True), 45, "Restart did not recalculate the current rule")
    rule = patch(**window(False))
    wait_for(lambda: applied(False), 45, "Restored rule was not applied")
    stop()
    passed("A43_worker_restart_recalculates_current_rule_and_preserves_history")

    for lost in (True, False):
        report["phase"] = "A43_loss_inside_collection" if lost else "A43_stop_inside_collection"
        current = uuid4().hex
        probe("create", current)
        process = launch_probe("run", current)
        try:

            def reached(process=process, current=current):
                require(process.poll() is None, "Gated worker exited before the source-read gate")
                return probe("control", current)["ready"]

            wait_for(reached, 60, "Worker did not reach the real source-read gate")
            before = probe("state")
            operations = probe("control", current)["operations"]
            if lost:
                rule = patch(**window(True))
                require(
                    probe("lose", current)["terminated"], "Owned singleton was not disconnected"
                )
            else:
                probe("control", current, action="stop")
            probe("control", current, action="gate-release")
            require(
                process.wait(timeout=45) == (4 if lost else 0),
                "Unexpected worker exit after gated failure",
            )
            after = probe("state")
            require(
                after["workers"] == 0
                and before["counts"] == after["counts"]
                and before["proof"] == after["proof"],
                "Stopped/lost worker wrote a checkpoint or result",
            )
            require(
                probe("control", current)["operations"] == operations,
                "Worker started another source operation after stop or lock loss",
            )
            require(state(False), "A later rule was sent after stop or lock loss")
            passed(
                "A43_lock_loss_inside_A3_blocks_HTTP_SQL_commit_and_B2"
                if lost
                else "A43_stop_inside_A3_is_graceful_and_blocks_new_operations"
            )
        finally:
            if process.poll() is None:
                probe("control", current, action="stop")
                probe("control", current, action="gate-release")
                try:
                    process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    # Terminate only this owned API container, including its stuck exec child.
                    stack.assert_owned()
                    stack.compose("stop", "--timeout", "15", "api", timeout=25)
                    process.wait(timeout=15)

    start()
    wait_for(lambda: applied(True), 45, "Recovery did not apply the current rule")
    rule = patch(**window(False))
    wait_for(lambda: applied(False), 45, "Final resume was not confirmed")
    api.request(
        "DELETE",
        f"/api/rules/{rule['id']}?expected_version={rule['version']}",
        headers=headers,
        expected=204,
    )
    stop()
    check_config(original, fixture.probe("snapshot"), False)
    require(
        api.request("GET", "/api/events")["total"] > 0
        and api.request("GET", "/api/snapshots?scope=node")["total"] >= 4,
        "Shared worker did not preserve collection history",
    )
    report["worker"] = {
        "singleton_rejected": True,
        "loss_within_collection": True,
        "stop_within_collection": True,
        "configuration_restored": True,
        "test_intervals_seconds": {"observations": 1, "reconciliation": 1},
    }
    passed("A43_recovery_history_and_four_folder_configuration_restored")
