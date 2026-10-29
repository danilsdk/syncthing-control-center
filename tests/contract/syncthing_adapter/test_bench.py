"""No live Syncthing: protect bench ownership, verified binaries and fixture privacy."""

import hashlib
import importlib.util
import os
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "deploy/syncthing"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("scc_a11_prepare", TOOLS / "prepare.py")
with patch.dict(sys.modules, {"prepare": prepare}):
    capture = load("scc_a11_capture", TOOLS / "capture.py")
    with patch.dict(sys.modules, {"capture": capture}):
        bench = load("scc_a11_bench", TOOLS / "bench.py")


def test_bad_release_digest_cannot_extract_an_executable(tmp_path, monkeypatch):
    asset = prepare.release()["assets"][prepare.platform_name()]
    archive = tmp_path / "downloads" / asset["name"]
    archive.parent.mkdir()
    archive.write_bytes(b"not the trusted archive")
    monkeypatch.setattr(prepare, "RUNTIME", tmp_path)
    monkeypatch.setattr(prepare, "extract_binary", lambda *_: pytest.fail("Unverified extraction"))
    with pytest.raises(prepare.BenchError, match="SHA-256 mismatch"):
        prepare.prepare()
    assert not (tmp_path / "bin").exists()


def test_archive_is_never_extracted_as_a_directory_tree(tmp_path):
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("../../outside.txt", "must not be written")
        source.writestr("release/syncthing.exe", b"chosen binary")
        source.writestr("release/other.exe", b"not chosen")
    assert prepare.extract_binary(archive, windows=True) == b"chosen binary"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["archive.zip"]


def test_duplicate_binary_is_rejected(tmp_path):
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("one/syncthing.exe", b"first")
        source.writestr("two/syncthing.exe", b"second")
    with pytest.raises(prepare.BenchError, match="exactly one"):
        prepare.extract_binary(archive, windows=True)


def test_correct_archive_digest_passes(tmp_path):
    archive = tmp_path / "archive"
    content = b"controlled fixture"
    archive.write_bytes(content)
    prepare.verify_archive(archive, hashlib.sha256(content).hexdigest())


def test_fixture_export_rejects_secrets_before_writing_any_file(tmp_path):
    output = tmp_path / "public"
    with pytest.raises(prepare.BenchError, match="private data"):
        capture.publish(
            output, {"ok.json": {"safe": True}, "bad.json": {"x": "secret-key"}}, {}, ["secret-key"]
        )
    assert not output.exists()


@pytest.mark.parametrize("value", [r"D:\Users\name\private", 'key-with-quote-"-and-backslash-\\'])
def test_fixture_guard_checks_json_escaped_values(value):
    with pytest.raises(prepare.BenchError, match="private data"):
        capture.assert_public({"nested": [value]}, [value])


def test_fixture_filename_cannot_escape_export_directory(tmp_path):
    with pytest.raises(prepare.BenchError, match="filename"):
        capture.publish(tmp_path / "public", {"../outside.json": {}}, {}, [])
    assert not (tmp_path / "outside.json").exists()


def test_upstream_personal_fields_and_arbitrary_events_are_removed():
    raw = {
        "myID": "demo",
        "startTime": "2026-10-24T12:00:00Z",
        "uptime": 1,
        "tilde": r"D:\Users\private",
        "apikey": "secret-key",
        "hostname": "personal-host",
    }
    system = capture.sanitize("system-status", raw)
    assert set(system) == {"myID", "startTime", "uptime"}
    events = capture.sanitize(
        "events",
        [
            {
                "id": 1,
                "type": "LocalChangeDetected",
                "time": "2026-10-24T12:00:00Z",
                "data": {"folder": "scc-a1-shared", "path": "private.txt", "from": "private-path"},
            },
            {
                "id": 2,
                "type": "StateChanged",
                "time": "2026-10-24T12:00:01Z",
                "data": {
                    "folder": "scc-a1-shared",
                    "from": "scanning",
                    "to": "idle",
                    "state": "/private/path",
                    "error": "secret-key",
                },
            },
        ],
    )
    assert events[0]["data"] == {"folder": "scc-a1-shared"}
    assert events[1]["data"] == {"folder": "scc-a1-shared", "from": "scanning", "to": "idle"}
    capture.assert_public([system, events], ["secret-key", "personal-host", "private.txt"])


def test_stale_process_identity_never_sends_shutdown_or_terminates(tmp_path, monkeypatch):
    identity = bench.process_identity(os.getpid())
    stale = dict(identity, created="different process")
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "BenchClient", lambda *_: pytest.fail("Unowned shutdown"))
    state = {
        "schema_version": 1,
        "bench": "scc-a1",
        "nodes": [{"name": "node-a", "process": stale}],
    }
    assert bench.stop(state) == {"stopped": [], "running": []}


def test_even_matching_pid_outside_runtime_cannot_be_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    node = {"process": bench.process_identity(os.getpid())}
    with pytest.raises(prepare.BenchError, match="outside its private runtime"):
        bench.owned(node)


def test_configuration_uses_certificate_id_when_saved_xml_lists_the_peer_first(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    owner = {"bench": "scc-a1", "owner": "test-owner"}
    bench.write_json(tmp_path / "owner.json", owner)
    identities = {"node-a": "-".join(["AAAAAAA"] * 8), "node-b": "-".join(["BBBBBBB"] * 8)}
    for name in identities:
        home = tmp_path / "nodes" / name
        bench.write_json(home / "scc-owned-home.json", owner)
        config = ET.fromstring(
            "<configuration><gui><apikey>demo-key</apikey></gui><options/>"
            '<defaults><folder><minDiskFree unit="B">0</minDiskFree></folder></defaults>'
            "</configuration>"
        )
        for identity in identities.values():
            ET.SubElement(config, "device", id=identity)
        ET.ElementTree(config).write(home / "config.xml", encoding="utf-8")

    def native(_binary, command, flag, home):
        assert command == "device-id" and flag == "--home"
        return identities[Path(home).name] + "\n"

    monkeypatch.setattr(bench, "native", native)
    result = bench.configure(Path("not-executed"), {"nodes": []})
    assert {node["name"]: node["device_id"] for node in result["nodes"]} == identities


def test_occupied_port_refuses_start_without_touching_other_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "prepare", lambda: pytest.fail("Unexpected binary preparation"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(bench, "NODES", (("node-a", port, 22101),))
        with pytest.raises(prepare.BenchError, match="occupied"):
            bench.start()
        assert listener.getsockname()[1] == port


def test_start_owns_worker_directly_without_inheriting_user_syncthing_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "NODES", (("node-a", 0, 0),))
    home = tmp_path / "nodes/node-a"
    home.mkdir(parents=True)
    state = {"nodes": [{"name": "node-a", "home": str(home), "device_id": "demo"}]}
    binary = tmp_path / "bin/syncthing.exe"
    monkeypatch.setattr(bench, "prepare", lambda: binary)
    monkeypatch.setattr(bench, "native", lambda *_: bench.release()["version"])
    monkeypatch.setattr(bench, "configure", lambda *_: state)
    monkeypatch.setattr(bench, "process_identity", lambda pid: {"pid": pid})
    monkeypatch.setattr(bench, "owned", lambda node: bool(node.get("process")))
    monkeypatch.setattr(
        bench, "BenchClient", lambda _: SimpleNamespace(get=lambda _: {"myID": "demo"})
    )
    launches = []

    def launch(args, **kwargs):
        launches.append((args, kwargs["env"]))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(bench.subprocess, "Popen", launch)
    monkeypatch.setenv("STMONITORED", "no")
    monkeypatch.setenv("STHOMEDIR", "foreign-home")
    monkeypatch.setenv("STGUIAPIKEY", "foreign-key")
    assert "STMONITORED" not in bench.clean_environment()  # CLI probes stay ordinary.
    assert bench.start() is state
    args, env = launches[0]
    assert args[:4] == [str(binary), "serve", "--home", str(home)]
    assert env["STMONITORED"] == "yes"
    assert not {"STHOMEDIR", "STGUIAPIKEY"} & env.keys()
    assert os.environ["STMONITORED"] == "no"


def test_endpoint_in_state_cannot_redirect_credentials_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    node = {
        "name": "node-a",
        "api_port": "18384@outside.invalid",
        "sync_port": 22101,
        "home": str(tmp_path / "nodes/node-a"),
    }
    with pytest.raises(prepare.BenchError, match="outside the fixed private bench"):
        bench.BenchClient(node)


def make_client(tmp_path, monkeypatch, api_port=18384):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "NODES", (("node-a", api_port, 22101),))
    home = tmp_path / "nodes/node-a"
    home.mkdir(parents=True)
    (home / "config.xml").write_text(
        "<configuration><gui><apikey>demo-key</apikey></gui></configuration>", encoding="utf-8"
    )
    return bench.BenchClient(
        {"name": "node-a", "api_port": api_port, "sync_port": 22101, "home": str(home)}
    )


def test_a11_client_cannot_send_paused_patch(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        client.opener, "open", lambda *_a, **_k: pytest.fail("Unexpected HTTP write")
    )
    with pytest.raises(prepare.BenchError, match="does not permit"):
        client.request("/rest/config/folders/scc-a1-shared", method="PATCH")


@contextmanager
def server(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as instance:
        thread = Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            thread.join(timeout=5)


def test_redirect_does_not_forward_api_key_to_a_second_server(tmp_path, monkeypatch):
    received = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("X-API-Key"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with server(Sink) as sink:

        class Redirect(Sink):
            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{sink.server_port}/rest/system/status"
                )
                self.end_headers()

        with server(Redirect) as redirect:
            result = make_client(tmp_path, monkeypatch, redirect.server_port).request(
                "/rest/system/status"
            )
            assert result["status_code"] == 302
    assert received == []


def link_directory(link, target):
    """Exercise real Windows junctions without requiring symlink privileges."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                "-Path $env:SCC_TEST_LINK -Target $env:SCC_TEST_TARGET | Out-Null",
            ],
            env=dict(os.environ, SCC_TEST_LINK=str(link), SCC_TEST_TARGET=str(target)),
            capture_output=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def linked_home(tmp_path, level):
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    suffix = {"runtime": "nodes/node-a", "nodes": "node-a", "home": "."}[level]
    destination = outside / suffix
    destination.mkdir(parents=True, exist_ok=True)
    link = {"runtime": runtime, "nodes": runtime / "nodes", "home": runtime / "nodes/node-a"}[level]
    link_directory(link, outside)
    return runtime, destination


@pytest.mark.parametrize("level", ["runtime", "nodes", "home"])
@pytest.mark.parametrize("home_form", ["lexical", "resolved"])
def test_linked_home_cannot_supply_client_credentials(tmp_path, monkeypatch, level, home_form):
    runtime, outside = linked_home(tmp_path, level)
    monkeypatch.setattr(bench, "RUNTIME", runtime)
    config = outside / "config.xml"
    content = "<configuration><gui><apikey>foreign-fixture-key</apikey></gui></configuration>"
    config.write_text(content, encoding="utf-8")
    home = runtime / "nodes/node-a" if home_form == "lexical" else outside
    with pytest.raises(prepare.BenchError):
        bench.BenchClient(
            {"name": "node-a", "home": str(home), "api_port": 18384, "sync_port": 22101}
        )
    assert config.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("level", ["runtime", "nodes", "home"])
def test_linked_home_cannot_receive_configuration(tmp_path, monkeypatch, level):
    runtime, outside = linked_home(tmp_path, level)
    monkeypatch.setattr(bench, "RUNTIME", runtime)
    monkeypatch.setattr(bench, "STATE", runtime / "state.json")
    native_calls = []

    def native(*args):
        native_calls.append(args)
        raise prepare.BenchError("Fixture CLI must not be reached")

    monkeypatch.setattr(bench, "native", native)
    with pytest.raises(prepare.BenchError):
        bench.configure(runtime / "bin/unused", {"nodes": []})
    assert native_calls == []
    assert list(outside.iterdir()) == []


def test_linked_home_after_client_creation_cannot_send_http(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    client = make_client(runtime, monkeypatch)
    home = runtime / "nodes/node-a"
    (home / "config.xml").unlink()
    home.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link_directory(home, outside)
    monkeypatch.setattr(client.opener, "open", lambda *_a, **_k: pytest.fail("Foreign HTTP"))
    with pytest.raises(prepare.BenchError):
        client.get("/rest/system/status")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("level", ["bin", "version"])
@pytest.mark.parametrize("binary_form", ["lexical", "resolved"])
def test_linked_binary_cannot_authorize_shutdown(tmp_path, monkeypatch, level, binary_form):
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = runtime / "bin" if level == "bin" else runtime / "bin/version"
    link_directory(link, outside)
    executable = outside / "syncthing"
    executable.write_bytes(b"fixture executable is never started")
    binary = link / executable.name if binary_form == "lexical" else executable
    identity = {"pid": 123456789, "created": "fixture", "executable": str(binary)}
    monkeypatch.setattr(bench, "RUNTIME", runtime)
    monkeypatch.setattr(bench, "STATE", runtime / "state.json")
    monkeypatch.setattr(bench, "process_identity", lambda _: identity)
    monkeypatch.setattr(bench, "BenchClient", lambda _: pytest.fail("Foreign shutdown"))
    with pytest.raises(prepare.BenchError):
        bench.stop({"nodes": [{"name": "node-a", "process": identity}]})
    assert executable.read_bytes() == b"fixture executable is never started"


def test_linked_bin_cannot_receive_prepared_executable(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    cache = runtime / "downloads"
    cache.mkdir(parents=True)
    archive = cache / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("release/syncthing.exe", b"verified fixture executable")
    outside = tmp_path / "outside"
    outside.mkdir()
    link_directory(runtime / "bin", outside)
    metadata = {
        "version": "fixture",
        "assets": {
            "windows-amd64": {
                "name": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        },
    }
    monkeypatch.setattr(prepare, "RUNTIME", runtime)
    monkeypatch.setattr(prepare, "release", lambda: metadata)
    monkeypatch.setattr(prepare, "platform_name", lambda: "windows-amd64")
    with pytest.raises(prepare.BenchError):
        prepare.prepare()
    assert list(outside.iterdir()) == []
