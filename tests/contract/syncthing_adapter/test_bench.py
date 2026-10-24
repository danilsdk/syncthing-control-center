"""No live Syncthing: protect bench ownership, verified binaries and fixture privacy."""

import hashlib
import importlib.util
import os
import socket
import sys
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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
