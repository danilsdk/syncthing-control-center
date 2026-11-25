"""Offline guard tests; these do not claim Docker/Compose integration passed."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stack_tools(tmp_path, monkeypatch):
    module = load("a4_stack_tools", "scripts/dev_stack.py")
    monkeypatch.setattr(module, "RUNTIME", tmp_path)
    return module


@pytest.mark.parametrize(
    ("engine", "platform"),
    [
        (b"linux/x86_64\n", "linux/amd64"),
        (b"linux/amd64\r\n", "linux/amd64"),
        (b"linux/aarch64\n", "linux/arm64"),
        (b"linux/arm64\r\n", "linux/arm64"),
    ],
)
def test_selects_platform_from_docker_engine(stack_tools, engine, platform):
    assert stack_tools.linux_platform(engine) == platform


@pytest.mark.parametrize("engine", [b"windows/x86_64", b"linux/riscv64", b""])
def test_rejects_unsupported_docker_engine(stack_tools, engine):
    with pytest.raises(stack_tools.StackError, match="Linux Docker Engine"):
        stack_tools.linux_platform(engine)


def test_prepare_reuses_settings_and_refuses_a_different_port(stack_tools):
    first = stack_tools.prepare("scc-a4-test-12345678", 18000)
    directory = stack_tools.project_directory(first["project"])
    before = {path.name: path.read_bytes() for path in (directory / "private").iterdir()}
    assert stack_tools.prepare(first["project"], 18000) == first
    assert before == {path.name: path.read_bytes() for path in (directory / "private").iterdir()}
    with pytest.raises(stack_tools.StackError):
        stack_tools.prepare(first["project"], 18001)
    with pytest.raises(stack_tools.StackError):
        stack_tools.prepare(first["project"], 18000, 18001)


def test_upgradable_runtime_keeps_its_api_port_and_owner(stack_tools):
    project = "scc-a4-test-12345678"
    state = stack_tools.prepare(project, 8080)
    assert state["ui_port"] == 8081
    marker = stack_tools.project_directory(project) / "state.json"
    marker.write_text(json.dumps({key: value for key, value in state.items() if key != "ui_port"}))
    assert stack_tools.load_state(project) == state
    assert stack_tools.prepare(project, 8080) == state


def test_ui_and_api_cannot_share_a_published_port(stack_tools):
    with pytest.raises(stack_tools.StackError):
        stack_tools.prepare("scc-a4-dev", 8000, 8000)
    assert not list(stack_tools.RUNTIME.iterdir())


def test_private_settings_have_portable_lf_bytes(stack_tools):
    state = stack_tools.prepare("scc-a4-test-12345678", 18000)
    private = stack_tools.project_directory(state["project"]) / "private"
    for path in private.iterdir():
        value = path.read_bytes()
        assert value.endswith(b"\n") and b"\r" not in value
        # Simulate Bash command substitution used by the PostgreSQL entrypoint.
        assert value.rstrip(b"\n").decode() == path.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("name", ["../scc-a4-dev", "scc-a4-../dev", "other", "", "scc-a4-test-x"])
def test_invalid_project_cannot_create_or_clean_a_directory(stack_tools, name):
    with pytest.raises(stack_tools.StackError):
        stack_tools.project_directory(name)
    assert not list(stack_tools.RUNTIME.iterdir())


def test_unmarked_or_mismatched_runtime_is_not_overwritten(stack_tools):
    directory = stack_tools.project_directory("scc-a4-dev")
    directory.mkdir()
    sentinel = directory / "user-file"
    sentinel.write_text("keep")
    with pytest.raises(stack_tools.StackError):
        stack_tools.prepare("scc-a4-dev", 8000)
    assert sentinel.read_text() == "keep"


def test_owner_marker_is_checked_before_use(stack_tools):
    state = stack_tools.prepare("scc-a4-dev", 8000)
    marker = stack_tools.project_directory(state["project"]) / "private/owner.json"
    marker.chmod(0o600)
    marker.write_text(json.dumps({"project": "different"}))
    with pytest.raises(stack_tools.StackError):
        stack_tools.load_state(state["project"])


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
def test_foreign_docker_resource_is_rejected_before_mutation(stack_tools, kind):
    stack = object.__new__(stack_tools.Stack)
    stack.state, stack.project = {"owner": "our-owner"}, "scc-a4-dev"
    labels = {
        "io.scc.owner": "foreign",
        "io.scc.stage": "a4-core",
        "com.docker.compose.project": stack.project,
    }
    entry = {"Config": {"Labels": labels}} if kind == "container" else {"Labels": labels}
    stack.inventory = lambda: {kind: [entry]}
    with pytest.raises(stack_tools.StackError):
        stack.assert_owned()


@pytest.mark.parametrize("value", ["", "\x00", "x" * 16385])
def test_container_secret_reader_has_bounds(tmp_path, value):
    module = load("a4_entrypoint", "deploy/compose/image/entrypoint.py")
    (tmp_path / "probe").write_text(value)
    with pytest.raises(ValueError):
        module.secret("probe", tmp_path)


def test_container_rejects_owner_for_another_project(tmp_path, monkeypatch):
    module = load("a4_entrypoint", "deploy/compose/image/entrypoint.py")
    monkeypatch.setenv("SCC_PROJECT", "scc-a4-dev")
    (tmp_path / "owner").write_text(json.dumps({"schema": 1, "project": "foreign", "owner": "x"}))
    with pytest.raises(ValueError):
        module.ownership(tmp_path)
