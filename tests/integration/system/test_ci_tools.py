"""Failure, credential and ownership guards for the application CI entry point."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ci():
    spec = importlib.util.spec_from_file_location(
        "ci_guards", ROOT / "scripts/check_application.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_work_is_preserved(ci, tmp_path):
    work = tmp_path / "existing"
    work.mkdir()
    sentinel = work / "user-data"
    sentinel.write_text("keep")
    with pytest.raises(ci.CheckFailed):
        ci.prepare(work, tmp_path / "reports", docker="docker")
    assert sentinel.read_text() == "keep"


def test_public_reports_cannot_contain_private_work(ci, tmp_path):
    with pytest.raises(ci.CheckFailed):
        ci.prepare(tmp_path / "work", tmp_path / "work/reports", docker="docker")
    assert not (tmp_path / "work").exists()


def test_private_state_cannot_be_created_in_checkout(ci, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(ci, "ROOT", checkout)
    with pytest.raises(ci.CheckFailed):
        ci.prepare(checkout / "private", tmp_path / "reports", docker="docker")
    assert not checkout.exists()


def test_changed_checkout_marker_is_rejected(ci, tmp_path):
    work, reports = tmp_path / "work", tmp_path / "reports"
    state = ci.prepare(work, reports, docker="docker")
    state["root"] = str(tmp_path)
    ci.write_json(work / "owner.json", state)
    with pytest.raises(ci.CheckFailed):
        ci.load_state(work, reports)


def test_foreign_pg_is_rejected_without_mutation(ci, tmp_path):
    runner = ci.Runner(ci.prepare(tmp_path / "work", tmp_path / "reports", docker="docker"))
    called = []

    def command(args, **kwargs):
        called.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {"io.scc.owner": "another-owner", "io.scc.stage": "a4-ci-pg"}
                        }
                    }
                ]
            ).encode(),
        )

    runner.command = command
    with pytest.raises(ci.CheckFailed):
        runner.inspect_pg()
    assert len(called) == 1 and "inspect" in called[0]


@pytest.mark.parametrize(
    "attributes",
    ['tests="0"', 'tests="1" skipped="1"', 'tests="1" errors="1"', 'tests="1" failures="1"'],
)
def test_nonpassing_junit_is_not_green(ci, tmp_path, attributes):
    source = tmp_path / "raw.xml"
    source.write_text(f"<testsuites><testsuite {attributes}/></testsuites>")
    with pytest.raises(ci.CheckFailed):
        ci.junit(source, tmp_path / "public.xml")


def test_actual_database_password_is_redacted_from_public_xml(ci, tmp_path):
    source, public = tmp_path / "raw.xml", tmp_path / "public.xml"
    password = "per-run-password-canary"
    source.write_text(
        '<testsuites><testsuite tests="1"><testcase><system-out>'
        f"postgresql://scc_test:{password}@127.0.0.1:5555/db"
        "</system-out></testcase></testsuite></testsuites>"
    )
    assert ci.junit(source, public, [password])["tests"] == 1
    assert password.encode() not in public.read_bytes()
    assert b"[REDACTED]" in public.read_bytes()


def test_working_database_and_compose_environment_are_removed(ci, monkeypatch):
    for name in (
        "SCC_DATABASE_URL",
        "SCC_AUTH_TEST_DATABASE_URL",
        "PGHOST",
        "COMPOSE_PROJECT_NAME",
    ):
        monkeypatch.setenv(name, "working-setting")
    env = ci.clean_env()
    assert not any(name.upper().startswith(("SCC_", "PG", "COMPOSE_")) for name in env)
    assert env["PYTHONPATH"] == str(ROOT / "backend")


def test_successful_cleanup_cannot_turn_failed_run_green(ci, tmp_path, monkeypatch):
    work, reports = tmp_path / "work", tmp_path / "reports"
    ci.prepare(work, reports, docker="docker")
    ci.write_json(reports / "summary.json", {"result": "failed", "phase": "postgres"})
    monkeypatch.setattr(ci.Runner, "cleanup", lambda self, **kw: {"result": "passed"})
    monkeypatch.setattr(ci.signal, "signal", lambda *args: None)
    monkeypatch.setattr(
        ci.sys, "argv", ["ci", "cleanup", "--work-dir", str(work), "--report-dir", str(reports)]
    )
    assert ci.main() == 0
    summary = json.loads((reports / "summary.json").read_text())
    assert summary["result"] == "failed" and summary["cleanup"]["result"] == "passed"


def test_failed_private_removal_cannot_report_success(ci, tmp_path, monkeypatch):
    work, reports = tmp_path / "work", tmp_path / "reports"
    runner = ci.Runner(ci.prepare(work, reports, docker="docker"))
    monkeypatch.setattr(runner, "inspect_pg", lambda: None)
    monkeypatch.setattr(runner, "command", lambda *a, **kw: SimpleNamespace(stdout=b""))

    def cannot_remove(path):
        raise PermissionError("controlled removal failure")

    monkeypatch.setattr(ci.shutil, "rmtree", cannot_remove)
    with pytest.raises(PermissionError):
        runner.cleanup(remove_private=True)
    assert work.exists() and not (reports / "cleanup.json").exists()
