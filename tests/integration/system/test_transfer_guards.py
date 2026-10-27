"""Offline failure guards; these are separate from actual file-transfer evidence."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def transfer():
    return load("transfer_guards", "tests/integration/system/check_transfer.py")


def configuration():
    return [
        [
            {"id": folder, "paused": False, "path": "/demo/" + folder}
            for folder in ("scc-a4-shared", "scc-a4-control")
        ]
        for _ in range(2)
    ]


@pytest.mark.parametrize("node,folder", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_any_unrelated_config_change_fails(transfer, node, folder):
    before = configuration()
    after = deepcopy(before)
    after[0][0]["paused"] = True
    after[node][folder]["path"] = "/unexpected"
    with pytest.raises(transfer.TransferError, match="non-target"):
        transfer.check_config(before, after, True)


def test_failure_after_pause_still_restores_original_config(transfer):
    class Fixture:
        paused = False
        created = 0

        def probe(self, op, **values):
            if op == "pause":
                self.paused = values["paused"]
            if op == "snapshot":
                value = configuration()
                value[0][0]["paused"] = self.paused
                return value
            if op == "state":
                return {
                    "paused": self.paused,
                    "requires_restart": False,
                    "identities": ["fixed-a", "fixed-b"],
                    "runtime": {"state": "idle"},
                }

        def create(self, folder):
            self.created += 1
            if self.created == 2:
                assert self.paused
                raise transfer.TransferError("Controlled stimulus failure")
            return {"file_name": "baseline", "sha256": "fixed-digest"}

        def file(self, *args):
            return "fixed-digest"

    fixture, evidence = Fixture(), {}
    with pytest.raises(transfer.TransferError, match="Controlled stimulus failure"):
        transfer.verify(fixture, lambda *a, **kw: None, evidence)
    assert not fixture.paused and evidence["restoration"] == {"attempted": True, "result": "passed"}


@pytest.mark.parametrize("name", ["../file", "/file", "a4-transfer-x.bin"])
def test_unsafe_filename_never_executes_in_container(transfer, name):
    fixture = transfer.Fixture(None, [])
    with pytest.raises(transfer.TransferError, match="Unsafe"):
        fixture.file("b", transfer.SHARED, name, b"test")


@pytest.mark.parametrize("defect", ["missing_transfer", "duplicate", "restoration"])
def test_twelve_claimed_checks_cannot_hide_missing_evidence(transfer, defect):
    ci = load("transfer_ci_guard", "scripts/check_application.py")
    names = [*sorted(transfer.CHECKS), *[f"existing-{i}" for i in range(9)]]
    value = {
        "result": "passed",
        "checks": [{"name": name} for name in names],
        "transfer": {"restoration": {"result": "passed"}},
    }
    if defect == "missing_transfer":
        value["checks"][0]["name"] = "unrelated_check"
    elif defect == "duplicate":
        value["checks"][-1] = value["checks"][-2]
    else:
        value["transfer"]["restoration"]["result"] = "failed"
    with pytest.raises(ci.CheckFailed):
        ci.validate_core(value)
