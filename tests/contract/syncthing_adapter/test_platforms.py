"""Portable archive contracts and Darwin API doubles; native Mac proof is separate."""

import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import os
import socket
import stat
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "deploy/syncthing"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, TOOLS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("a10_prepare", "prepare.py")
with patch.dict(sys.modules, {"prepare": prepare}):
    capture = load("a10_capture", "capture.py")
    with patch.dict(sys.modules, {"capture": capture}):
        bench = load("a10_bench", "bench.py")


@pytest.mark.parametrize(
    "system,machine,target",
    [
        ("Windows", "AMD64", "windows-amd64"),
        ("Linux", "x86_64", "linux-amd64"),
        ("Darwin", "arm64", "macos-arm64"),
    ],
)
def test_platform_selects_its_pinned_format_without_changing_the_host(
    monkeypatch, system, machine, target
):
    monkeypatch.setattr(prepare.platform, "system", lambda: system)
    monkeypatch.setattr(prepare.platform, "machine", lambda: machine)
    monkeypatch.setattr(prepare, "macos_translated", lambda: False)
    asset = prepare.release()["assets"][prepare.platform_name()]
    assert prepare.platform_name() == target
    assert asset["name"].endswith("." + asset["archive_format"])
    assert asset["executable"] == ("syncthing.exe" if system == "Windows" else "syncthing")


@pytest.mark.parametrize("system,machine", [("Darwin", "x86_64"), ("Darwin", "i386")])
def test_intel_or_translated_python_cannot_select_an_arm_archive(monkeypatch, system, machine):
    monkeypatch.setattr(prepare.platform, "system", lambda: system)
    monkeypatch.setattr(prepare.platform, "machine", lambda: machine)
    with pytest.raises(prepare.BenchError, match="ARM64.*Rosetta"):
        prepare.platform_name()


def test_rosetta_observation_overrides_an_apparently_arm_machine(monkeypatch):
    monkeypatch.setattr(prepare.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prepare.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(prepare, "macos_translated", lambda: True)
    with pytest.raises(prepare.BenchError, match="Rosetta"):
        prepare.platform_name()


@pytest.mark.parametrize("system,machine", [("FreeBSD", "amd64"), ("Linux", "arm64")])
def test_unpinned_platform_is_refused(monkeypatch, system, machine):
    monkeypatch.setattr(prepare.platform, "system", lambda: system)
    monkeypatch.setattr(prepare.platform, "machine", lambda: machine)
    with pytest.raises(prepare.BenchError):
        prepare.platform_name()


@pytest.mark.parametrize(
    "value,error,expected", [(0, 0, False), (1, 0, True), (0, errno.ENOENT, False)]
)
def test_apple_translation_probe(monkeypatch, value, error, expected):
    def sysctl(name, output, size, _new, _length):
        assert name == b"sysctl.proc_translated"
        ctypes.cast(output, ctypes.POINTER(ctypes.c_int)).contents.value = value
        ctypes.set_errno(error)
        return -1 if error else 0

    monkeypatch.setattr(
        prepare.ctypes, "CDLL", lambda *_a, **_k: SimpleNamespace(sysctlbyname=sysctl)
    )
    assert prepare.macos_translated() is expected


def test_unknown_translation_state_is_not_reported_as_native(monkeypatch):
    def denied(*_args):
        ctypes.set_errno(errno.EPERM)
        return -1

    monkeypatch.setattr(
        prepare.ctypes, "CDLL", lambda *_a, **_k: SimpleNamespace(sysctlbyname=denied)
    )
    with pytest.raises(prepare.BenchError, match="Cannot verify native"):
        prepare.macos_translated()


def test_macos_zip_extracts_only_verified_unix_binary(tmp_path, monkeypatch):
    asset = prepare.release()["assets"]["macos-arm64"]
    archive = tmp_path / "downloads" / asset["name"]
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("release/syncthing", b"fixture arm executable")
        source.writestr("release/etc/freebsd-rc/syncthing", b"service script, not a binary")
        source.writestr("release/etc/firewall-ufw/syncthing", b"firewall config, not a binary")
        source.writestr("release/syncthing.exe", b"wrong platform")
        source.writestr("../../outside", b"not extracted")
    metadata = prepare.release()
    metadata["assets"]["macos-arm64"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(prepare, "RUNTIME", tmp_path)
    monkeypatch.setattr(prepare, "platform_name", lambda: "macos-arm64")
    monkeypatch.setattr(prepare, "release", lambda: metadata)
    binary = prepare.prepare()
    assert binary.name == "syncthing" and binary.read_bytes() == b"fixture arm executable"
    assert not (tmp_path / "outside").exists()
    archive.write_bytes(b"corrupt cache")
    with pytest.raises(prepare.BenchError, match="SHA-256 mismatch"):
        prepare.prepare()
    assert binary.read_bytes() == b"fixture arm executable"


def test_linux_tar_remains_supported(tmp_path):
    archive = tmp_path / "linux.tar.gz"
    with tarfile.open(archive, "w:gz") as source:
        entry = tarfile.TarInfo("release/syncthing")
        entry.size = 6
        source.addfile(entry, io.BytesIO(b"binary"))
        entry = tarfile.TarInfo("release/etc/freebsd-rc/syncthing")
        entry.size = 7
        source.addfile(entry, io.BytesIO(b"service"))
    assert (
        prepare.extract_binary(archive, archive_format="tar.gz", executable="syncthing")
        == b"binary"
    )


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "large", "empty"])
def test_mac_zip_rejects_ambiguous_or_unsafe_executable(tmp_path, monkeypatch, kind):
    archive = tmp_path / "mac.zip"
    monkeypatch.setattr(prepare, "MAX_BINARY_BYTES", 8)
    with zipfile.ZipFile(archive, "w") as source:
        info = zipfile.ZipInfo("release/syncthing")
        if kind == "symlink":
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        source.writestr(info, b"" if kind == "empty" else b"a" * (9 if kind == "large" else 3))
        if kind == "duplicate":
            source.writestr("another/syncthing", b"second")
    with pytest.raises(prepare.BenchError):
        prepare.extract_binary(archive, archive_format="zip", executable="syncthing")


def darwin_library(
    monkeypatch, executable, *, starts=(100, 100), error=0, partial=False, path_error=0
):
    observations = iter(starts)

    def info(pid, flavor, _arg, output, size):
        assert flavor == 3 and size == 136
        if error:
            ctypes.set_errno(error)
            return 0
        value = ctypes.cast(output, ctypes.POINTER(bench.ProcBSDInfo)).contents
        value.pid, value.status, value.start_sec, value.start_usec = pid, 2, next(observations), 7
        return size - 1 if partial else size

    def path(_pid, buffer, size):
        assert size == 4096
        if path_error:
            ctypes.set_errno(path_error)
            return 0
        buffer.value = os.fsencode(executable)
        return len(buffer.value)

    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        bench.ctypes,
        "CDLL",
        lambda *_a, **_k: SimpleNamespace(proc_pidinfo=info, proc_pidpath=path),
    )


def test_darwin_identity_is_pid_microsecond_start_and_executable(tmp_path, monkeypatch):
    executable = tmp_path / "syncthing"
    executable.touch()
    darwin_library(monkeypatch, executable)
    assert bench.process_identity(123) == {
        "pid": 123,
        "created": "100:7",
        "executable": str(executable.resolve()),
    }


def test_pid_reuse_between_darwin_reads_does_not_return_an_identity(tmp_path, monkeypatch):
    darwin_library(monkeypatch, tmp_path / "unused", starts=(100, 101))
    assert bench.process_identity(123) is None


def test_darwin_reports_an_exited_pid_as_absent(tmp_path, monkeypatch):
    darwin_library(monkeypatch, tmp_path / "unused", error=errno.ESRCH)
    assert bench.process_identity(123) is None


@pytest.mark.parametrize("error,partial", [(errno.EPERM, False), (errno.ENOENT, False), (0, True)])
def test_unreadable_darwin_identity_is_not_treated_as_stopped(
    tmp_path, monkeypatch, error, partial
):
    darwin_library(monkeypatch, tmp_path / "unused", error=error, partial=partial)
    with pytest.raises(prepare.BenchError, match="Cannot verify macOS"):
        bench.process_identity(123)


def test_missing_executable_path_does_not_claim_a_live_darwin_pid_has_exited(tmp_path, monkeypatch):
    # proc_pidpath may propagate vnode lookup ENOENT for a still-live process.
    darwin_library(monkeypatch, tmp_path / "unlinked", path_error=errno.ENOENT)
    with pytest.raises(prepare.BenchError, match="Cannot verify macOS executable"):
        bench.process_identity(123)


@pytest.mark.parametrize("acknowledged", [False, True])
def test_failed_mac_shutdown_never_signals_or_rewrites_runtime(tmp_path, monkeypatch, acknowledged):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    executable = tmp_path / "bin/syncthing"
    executable.parent.mkdir()
    executable.touch()
    identity = {"pid": 123, "created": "100:7", "executable": str(executable)}
    state = {
        "bench": "scc-a1",
        "schema_version": 1,
        "nodes": [{"name": "node-a", "process": identity}],
    }
    bench.write_json(bench.STATE, state)
    original = bench.STATE.read_bytes()
    monkeypatch.setattr(bench, "process_identity", lambda _pid: identity)
    clock = Clock()
    monkeypatch.setattr(bench.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bench.time, "sleep", clock.sleep)
    monkeypatch.setattr(bench, "windows_process", lambda *_a, **_k: pytest.fail("Foreign signal"))
    monkeypatch.setattr(
        bench.os, "pidfd_open", lambda *_a: pytest.fail("Linux API on Mac"), raising=False
    )
    requests = []

    def request(path, **kwargs):
        requests.append((path, kwargs))
        if acknowledged:
            return {"status_code": 200}
        raise prepare.BenchError("shutdown unavailable")

    monkeypatch.setattr(bench, "BenchClient", lambda _: SimpleNamespace(request=request))
    with pytest.raises(prepare.BenchError, match="runtime retained"):
        bench.stop(state)
    assert requests == [("/rest/system/shutdown", {"method": "POST"})]
    assert bench.STATE.read_bytes() == original and executable.exists()
    assert 30 <= clock.now < 31
    report = json.loads((tmp_path / "shutdown-attempts.jsonl").read_text())
    assert report["result"] == "failed"
    assert report["nodes"][0]["exit_confirmed"] is False
    assert report["nodes"][0]["phase"] == "await_exit"


def test_identity_changed_while_opening_credentials_never_sends_shutdown(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    executable = tmp_path / "bin/syncthing"
    expected = {"pid": 123, "created": "first", "executable": str(executable)}
    current = [expected]
    monkeypatch.setattr(bench, "process_identity", lambda _: current[0])

    def client(_):
        current[0] = dict(expected, created="reused PID")
        return SimpleNamespace(request=lambda *_a, **_k: pytest.fail("Foreign shutdown"))

    monkeypatch.setattr(bench, "BenchClient", client)
    bench.stop({"nodes": [{"name": "node-a", "process": expected}]})


def test_failed_identity_after_spawn_is_persisted_and_cannot_claim_clean_stop(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "NODES", (("node-a", 0, 0),))
    home = tmp_path / "nodes/node-a"
    home.mkdir(parents=True)
    binary = tmp_path / "bin/syncthing"
    state = {"nodes": [{"name": "node-a", "home": str(home)}]}
    monkeypatch.setattr(bench, "prepare", lambda: binary)
    monkeypatch.setattr(bench, "native", lambda *_: bench.release()["version"])
    monkeypatch.setattr(bench, "configure", lambda *_: state)
    monkeypatch.setattr(bench.subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(pid=123))

    def unreadable(_):
        raise prepare.BenchError("OS identity temporarily unavailable")

    monkeypatch.setattr(bench, "process_identity", unreadable)
    monkeypatch.setattr(bench, "BenchClient", lambda _: pytest.fail("Unverified shutdown"))
    with pytest.raises(prepare.BenchError, match="unverified.*retained"):
        bench.start()
    assert '"pending_pid": 123' in bench.STATE.read_text()
    with pytest.raises(prepare.BenchError, match="unverified.*retained"):
        bench.stop(state)
    assert home.exists()


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("winerror", [None, 10013])
def test_initial_start_waits_for_closed_connections_without_reusing_ports(
    tmp_path, monkeypatch, winerror
):
    clock = Clock()
    monkeypatch.setattr(bench.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bench.time, "sleep", clock.sleep)
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "NODES", (("node-a", 18384, 22101),))
    checked = []

    class ExclusiveProbe:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bind(self, address):
            checked.append(address)
            if address[1] == 18384 and clock.now < 0.25:
                error = OSError(errno.EACCES if winerror else errno.EADDRINUSE, "port reserved")
                error.winerror = winerror
                raise error

    class PreparationReached(Exception):
        pass

    def prepare_after_free_ports():
        raise PreparationReached

    monkeypatch.setattr(bench.socket, "socket", ExclusiveProbe)
    monkeypatch.setattr(bench, "prepare", prepare_after_free_ports)
    with pytest.raises(PreparationReached):
        bench.start()
    assert 0.25 <= clock.now < 1
    assert ("127.0.0.1", 22101) in checked


def test_start_retains_a_real_foreign_listener_and_has_a_bounded_wait(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(bench.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bench.time, "sleep", clock.sleep)
    monkeypatch.setattr(bench, "PORT_WAIT_SECONDS", 0.25, raising=False)
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench, "prepare", lambda: pytest.fail("Foreign port must prevent startup"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(bench, "NODES", (("node-a", port, 0),))
        with pytest.raises(prepare.BenchError, match="reserved bench port"):
            bench.start()
        assert 0.25 <= clock.now <= 0.35
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            accepted, _ = listener.accept()
            accepted.close()
    assert not bench.STATE.exists()


def test_non_busy_bind_error_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench.time, "sleep", lambda _: pytest.fail("Permission error retried"))
    monkeypatch.setattr(bench, "prepare", lambda: pytest.fail("Unbound port allowed startup"))

    class DeniedProbe:
        def __enter__(self):
            raise OSError(errno.EACCES, "permission denied")

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(bench.socket, "socket", DeniedProbe)
    with pytest.raises(prepare.BenchError, match="Cannot check"):
        bench.start()


def test_mac_shutdown_waits_for_verified_exit_after_the_service_timeout(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(bench.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bench.time, "sleep", clock.sleep)
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    identity = {"pid": 123, "created": "100:7", "executable": str(tmp_path / "bin/syncthing")}
    monkeypatch.setattr(bench, "process_identity", lambda _: identity if clock.now < 12 else None)
    monkeypatch.setattr(
        bench,
        "BenchClient",
        lambda _: SimpleNamespace(request=lambda *_a, **_k: {"status_code": 200}),
    )
    monkeypatch.setattr(bench, "windows_process", lambda *_a, **_k: pytest.fail("Foreign signal"))
    monkeypatch.setattr(bench.os, "pidfd_open", lambda *_: pytest.fail("Linux API"), raising=False)
    result = bench.stop({"nodes": [{"name": "node-a", "process": identity}]})
    assert result == {"stopped": ["node-a"], "running": []}
    assert 12 <= clock.now < 13
    report = json.loads((tmp_path / "shutdown-attempts.jsonl").read_text())
    assert report["result"] == "passed"
    assert report["nodes"][0]["shutdown_status"] == 200
    assert report["nodes"][0]["exit_confirmed"] is True
    assert report["nodes"][0]["elapsed_seconds"] >= 12


def test_mac_shutdown_keeps_failed_attempt_when_a_later_cleanup_confirms_exit(
    tmp_path, monkeypatch
):
    clock = Clock()
    monkeypatch.setattr(bench.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bench.time, "sleep", clock.sleep)
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    identity = {"pid": 123, "created": "100:7", "executable": str(tmp_path / "bin/syncthing")}
    current = [identity]
    monkeypatch.setattr(bench, "process_identity", lambda _: current[0])

    def client(_):
        if clock.now >= 30:
            current[0] = None
        return SimpleNamespace(request=lambda *_a, **_k: {"status_code": 200})

    monkeypatch.setattr(bench, "BenchClient", client)
    state = {"nodes": [{"name": "node-a", "process": identity}]}
    with pytest.raises(prepare.BenchError, match="runtime retained"):
        bench.stop(state)
    assert bench.stop(state)["running"] == []
    reports = [
        json.loads(line) for line in (tmp_path / "shutdown-attempts.jsonl").read_text().splitlines()
    ]
    assert [item["result"] for item in reports] == ["failed", "passed"]
    assert reports[0]["nodes"][0]["exit_confirmed"] is False
    assert reports[1]["nodes"][0]["exit_confirmed"] is True


def test_unknown_mac_identity_during_shutdown_retains_state_and_does_not_confirm_exit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bench, "RUNTIME", tmp_path)
    monkeypatch.setattr(bench, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    identity = {"pid": 123, "created": "100:7", "executable": str(tmp_path / "bin/syncthing")}
    state = {
        "bench": "scc-a1",
        "schema_version": 1,
        "nodes": [{"name": "node-a", "process": identity}],
    }
    bench.write_json(bench.STATE, state)
    original = bench.STATE.read_bytes()
    known = [True]

    def observe(_):
        if not known[0]:
            raise prepare.BenchError("Cannot verify macOS process creation time")
        return identity

    def shutdown(*_a, **_k):
        known[0] = False
        return {"status_code": 200}

    monkeypatch.setattr(bench, "process_identity", observe)
    monkeypatch.setattr(bench, "BenchClient", lambda _: SimpleNamespace(request=shutdown))
    with pytest.raises(prepare.BenchError, match="Cannot verify macOS"):
        bench.stop(state)
    assert bench.STATE.read_bytes() == original
    report = json.loads((tmp_path / "shutdown-attempts.jsonl").read_text())
    assert report["result"] == "failed"
    assert report["nodes"][0]["exit_confirmed"] is False
