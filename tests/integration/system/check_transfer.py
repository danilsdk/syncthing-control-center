"""Real T-02–T-04 on two owned containers; independent of B rules and worker."""

import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

SHARED = "scc-a4-shared"
CONTROL = "scc-a4-control"
CHECKS = {
    "T02_real_file_transfer_sha256",
    "T03_target_paused_new_file_absent_control_transfers",
    "T04_adapter_resume_file_sha256_and_configuration_restored",
}
PROBE = Path(__file__).with_name("source_probe.py").read_text(encoding="utf-8")


class TransferError(Exception):
    """Only fixed public messages, without source configuration or command output."""


def require(value, message):
    if not value:
        raise TransferError(message)


def check_config(before, after, paused):
    expected = deepcopy(before)
    require(
        len(before) == 2
        and all({row["id"] for row in rows} == {SHARED, CONTROL} for rows in before),
        "Expected two demo folders on both sources",
    )
    for row in expected[0]:
        if row["id"] == SHARED:
            row["paused"] = paused
    # Compare every field, including paths, devices, options and control folders.
    require(after == expected, "A non-target configuration field changed")


class Fixture:
    def __init__(self, stack, ids):
        self.stack, self.ids = stack, ids

    def probe(self, op, **values):
        result = self.stack.compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            PROBE,
            input=json.dumps({"ids": self.ids, "op": op, **values}).encode(),
            timeout=35,
        )
        return json.loads(result.stdout)

    def file(self, node, folder, name, payload=None):
        require(
            node in {"a", "b"}
            and folder in {SHARED, CONTROL}
            and re.fullmatch(r"a4-transfer-[a-f0-9]{32}\.bin", name),
            "Unsafe disposable file address",
        )
        path = f"/var/syncthing/data/{folder}/{name}"
        # All script text is constant; the validated path is a positional argument.
        # noclobber refuses to overwrite even a pre-existing disposable sentinel.
        script = (
            'set -C; cat > "$1"'
            if payload is not None
            else 'if [ -e "$1" ]; then sha256sum "$1"; else exit 3; fi'
        )
        result = self.stack.compose(
            "exec",
            "-T",
            "--user",
            "1000:1000",
            "syncthing-" + node,
            "sh",
            "-c",
            script,
            "sh",
            path,
            input=payload,
            check=False,
            timeout=10,
        )
        if payload is None and result.returncode == 3:
            return None
        require(result.returncode == 0, "Disposable file operation failed")
        if payload is not None:
            return None
        digest = result.stdout.decode().split()[0]
        require(bool(re.fullmatch(r"[a-f0-9]{64}", digest)), "Invalid file digest")
        return digest

    def create(self, folder):
        name = "a4-transfer-" + uuid4().hex + ".bin"
        payload = (uuid4().bytes + b"SCC disposable transfer\n") * 2048
        require(self.file("a", folder, name) is None, "Receiver sentinel already exists")
        self.file("b", folder, name, payload)
        digest = hashlib.sha256(payload).hexdigest()
        require(self.file("b", folder, name) == digest, "Sender bytes differ")
        indexed = self.probe("scan", folder=folder, name=name)
        require(
            indexed == {"name": name, "size": len(payload), "deleted": False},
            "Sender did not index the new file",
        )
        return {"file_name": name, "bytes": len(payload), "sha256": digest}


def wait_for(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.25)
    raise TransferError(message)


def verify(fixture, passed, evidence):
    before = fixture.probe("snapshot")
    check_config(before, before, False)
    require(all(not row["paused"] for rows in before for row in rows), "Demo was paused")
    initial = fixture.probe("state")
    require(not initial["requires_restart"], "Demo requires a restart")
    identities = initial["identities"]

    def observed(paused):
        state = fixture.probe("state")
        require(state["identities"] == identities, "A source restarted during control")
        require(not state["requires_restart"], "Pause requires a restart")
        if state["paused"] is not paused:
            return None
        # A paused source can report an empty runtime state; keep this unknown.
        if not paused and state["runtime"].get("state") != "idle":
            return None
        return state

    def arrived(folder, file):
        digest = fixture.file("a", folder, file["file_name"])
        if digest is None:
            return False
        require(digest == file["sha256"], "Received file hash differs")
        return True

    succeeded = False
    try:
        wait_for(lambda: observed(False), 25, "Initial runtime was not idle")
        baseline = fixture.create(SHARED)
        start = time.monotonic()
        wait_for(lambda: arrived(SHARED, baseline), 60, "Baseline file did not synchronize")
        passed(
            "T02_real_file_transfer_sha256",
            **baseline,
            elapsed_seconds=round(time.monotonic() - start, 3),
        )

        fixture.probe("pause", paused=True)
        state = wait_for(lambda: observed(True), 15, "Paused configuration not observed")
        check_config(before, fixture.probe("snapshot"), True)
        sentinel = fixture.create(SHARED)
        control = fixture.create(CONTROL)
        start = time.monotonic()
        polls, control_arrived = 0, False
        while True:
            require(observed(True) is not None, "Target left paused configuration")
            require(
                fixture.file("a", SHARED, sentinel["file_name"]) is None,
                "New sentinel arrived while paused",
            )
            control_arrived = arrived(CONTROL, control) or control_arrived
            polls += 1
            elapsed = time.monotonic() - start
            if elapsed >= 5 and polls >= 3 and control_arrived:
                break
            require(elapsed < 60, "Control transfer did not complete while target was paused")
            time.sleep(0.25)
        check_config(before, fixture.probe("snapshot"), True)
        passed(
            "T03_target_paused_new_file_absent_control_transfers",
            absent_seconds=round(elapsed, 3),
            absence_checks=polls,
            sentinel=sentinel,
            control=control,
            config_paused=True,
            runtime=state["runtime"],
            three_other_configs_unchanged=True,
        )

        start = time.monotonic()
        fixture.probe("pause", paused=False)
        wait_for(lambda: observed(False), 25, "Resumed idle runtime not observed")
        wait_for(lambda: arrived(SHARED, sentinel), 60, "Sentinel did not synchronize after resume")
        evidence["resume"] = {**sentinel, "elapsed_seconds": round(time.monotonic() - start, 3)}
        succeeded = True
    finally:
        # Even a failed PATCH/observation may have changed the source. Restore
        # through the adapter, then compare all original folder configurations.
        evidence["restoration"] = {"attempted": True, "result": "failed"}
        fixture.probe("pause", paused=False)
        wait_for(lambda: observed(False), 25, "Original unpaused runtime not restored")
        require(fixture.probe("snapshot") == before, "Original folder configs not restored")
        evidence["restoration"]["result"] = "passed"
    if succeeded:
        passed(
            "T04_adapter_resume_file_sha256_and_configuration_restored",
            **evidence["resume"],
            four_original_configs_restored=True,
        )
