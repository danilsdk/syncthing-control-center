"""Two disposable loopback Syncthing nodes; this is lab tooling, not the A1 adapter."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from capture import assert_public, publish, sanitize
from prepare import HERE, RUNTIME, BenchError, platform_name, prepare, release

ROOT = HERE.parents[1]
STATE = RUNTIME / "state.json"
FOLDERS = ("scc-a1-shared", "scc-a1-control")
NODES = (("node-a", 18384, 22101), ("node-b", 28384, 22102))
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_state() -> dict:
    if not STATE.exists():
        return {"schema_version": 1, "bench": "scc-a1", "nodes": []}
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if value.get("bench") != "scc-a1" or value.get("schema_version") != 1:
        raise BenchError("Unrecognized bench state; existing files were not changed")
    return value


@contextmanager
def operation_lock():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "operation.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BenchError("Another bench operation is already running") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def windows_process(pid: int, expected: dict | None = None, terminate: bool = False):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    handle = kernel.OpenProcess(0x1000 | (0x0001 if terminate else 0), False, pid)
    if not handle:
        return None
    try:
        created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.GetProcessTimes(
            handle, *(ctypes.byref(t) for t in (created, ended, system, user))
        ):
            raise BenchError("Cannot verify bench process creation time")
        if ended.dwHighDateTime or ended.dwLowDateTime:
            return None
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise BenchError("Cannot verify bench executable")
        identity = {
            "pid": pid,
            "created": (created.dwHighDateTime << 32) | created.dwLowDateTime,
            "executable": str(Path(buffer.value).resolve()),
        }
        if terminate:
            if identity != expected:
                raise BenchError("Refusing to terminate a process with a different identity")
            if not kernel.TerminateProcess(handle, 0):
                raise BenchError("Cannot terminate the verified bench process")
        return identity
    finally:
        kernel.CloseHandle(handle)


def process_identity(pid: int | None):
    if not pid:
        return None
    if os.name == "nt":
        return windows_process(pid)
    try:
        source = Path(f"/proc/{pid}")
        fields = (source / "stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        return {
            "pid": pid,
            "created": fields[19],
            "executable": str((source / "exe").resolve(strict=True)),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned(node: dict) -> bool:
    identity = node.get("process")
    if not identity or process_identity(identity["pid"]) != identity:
        return False
    binary = Path(identity["executable"]).resolve()
    if not binary.is_relative_to((RUNTIME / "bin").resolve()):
        raise BenchError("Bench state refers to an executable outside its private runtime")
    return True


def clean_environment() -> dict:
    # Existing user Syncthing settings cannot override this disposable instance.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("ST")}
    env["GOMAXPROCS"] = "2"
    return env


def native(binary: Path, *args: str) -> str:
    result = subprocess.run(
        [str(binary), *args],
        env=clean_environment(),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise BenchError("Syncthing CLI failed; no raw output was published")
    return result.stdout


def set_text(parent, name: str, value: str) -> None:
    element = parent.find(name)
    if element is None:
        element = ET.SubElement(parent, name)
    element.text = value


def configure(binary: Path, previous: dict) -> dict:
    if any(owned(node) for node in previous["nodes"]):
        raise BenchError("Stop the owned bench before reconfiguring it")
    owner_file = RUNTIME / "owner.json"
    if not owner_file.exists():
        write_json(owner_file, {"bench": "scc-a1", "owner": str(uuid4())})
    owner = json.loads(owner_file.read_text(encoding="utf-8"))
    if owner.get("bench") != "scc-a1":
        raise BenchError("Unrecognized runtime owner marker")
    nodes = []
    for name, api_port, sync_port in NODES:
        home = RUNTIME / "nodes" / name
        stamp = home / "scc-owned-home.json"
        if home.exists() and any(home.iterdir()):
            if not stamp.exists() or json.loads(stamp.read_text(encoding="utf-8")) != owner:
                raise BenchError("Refusing an existing home without this bench owner marker")
        home.mkdir(parents=True, exist_ok=True)
        write_json(stamp, owner)
        if not (home / "config.xml").exists():
            native(binary, "generate", "--home", str(home), "--no-port-probing")
        # Syncthing sorts devices when saving config: the first is not necessarily self.
        device_id = native(binary, "device-id", "--home", str(home)).strip()
        if not re.fullmatch(r"[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}", device_id):
            raise BenchError("CLI did not return a valid certificate Device ID")
        nodes.append(
            {
                "name": name,
                "home": str(home.resolve()),
                "api_port": api_port,
                "sync_port": sync_port,
                "device_id": device_id,
            }
        )
    for node in nodes:
        peer = next(item for item in nodes if item is not node)
        document = ET.parse(Path(node["home"]) / "config.xml")
        root = document.getroot()
        gui = root.find("gui")
        set_text(gui, "address", f"127.0.0.1:{node['api_port']}")
        gui.set("tls", "false")
        set_text(gui, "metricsWithoutAuth", "false")
        options = root.find("options")
        for field, value in {
            "globalAnnounceEnabled": "false",
            "localAnnounceEnabled": "false",
            "relaysEnabled": "false",
            "natEnabled": "false",
            "urAccepted": "-1",
            "crashReportingEnabled": "false",
            "autoUpgradeIntervalH": "0",
            "upgradeToPreReleases": "false",
        }.items():
            set_text(options, field, value)
        for address in options.findall("listenAddress"):
            options.remove(address)
        ET.SubElement(options, "listenAddress").text = f"tcp://127.0.0.1:{node['sync_port']}"
        for device in list(root.findall("device")):
            if device.attrib["id"] != node["device_id"]:
                root.remove(device)
        if not root.findall("device"):
            ET.SubElement(root, "device", {"id": node["device_id"], "name": node["name"]})
        device = ET.SubElement(
            root,
            "device",
            {
                "id": peer["device_id"],
                "name": peer["name"],
                "compression": "metadata",
            },
        )
        ET.SubElement(device, "address").text = f"tcp://127.0.0.1:{peer['sync_port']}"
        for old in list(root.findall("folder")):
            root.remove(old)
        for folder_id in FOLDERS:
            folder = deepcopy(root.find("defaults/folder"))
            folder.attrib.update(
                id=folder_id,
                label=folder_id,
                path=str((Path(node["home"]) / "data" / folder_id).resolve()),
                type="sendreceive",
                rescanIntervalS="7200",
                fsWatcherEnabled="false",
            )
            for device in list(folder.findall("device")):
                folder.remove(device)
            for participant in nodes:
                ET.SubElement(
                    folder,
                    "device",
                    {"id": participant["device_id"], "introducedBy": ""},
                )
            set_text(folder, "paused", "false")
            minimum = folder.find("minDiskFree")
            minimum.text, minimum.attrib["unit"] = "0", "B"
            Path(folder.attrib["path"]).mkdir(parents=True, exist_ok=True)
            root.append(folder)
        document.write(Path(node["home"]) / "config.xml", encoding="utf-8", xml_declaration=True)
    value = {
        "schema_version": 1,
        "bench": "scc-a1",
        "owner": owner["owner"],
        "version": release()["version"],
        "nodes": nodes,
    }
    write_json(STATE, value)
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BenchClient:
    def __init__(self, node: dict):
        expected = next((item for item in NODES if item[0] == node.get("name")), None)
        if (
            expected is None
            or node.get("api_port") != expected[1]
            or node.get("sync_port") != expected[2]
            or Path(node["home"]).resolve() != (RUNTIME / "nodes" / expected[0]).resolve()
        ):
            raise BenchError("Refusing an endpoint outside the fixed private bench")
        self.node = node
        self.key = ET.parse(Path(node["home"]) / "config.xml").findtext("gui/apikey")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path: str, *, method: str = "GET", wrong_key: bool = False) -> dict:
        if (
            method not in {"GET", "POST"}
            or not path.startswith("/rest/")
            or path.startswith("//")
            or "\\" in path
            or (
                method == "POST"
                and path.split("?", 1)[0]
                not in {
                    "/rest/system/shutdown",
                    "/rest/db/scan",
                }
            )
        ):
            raise BenchError("A1.1 probe does not permit this API operation")
        key = "scc-a1-deliberately-wrong" if wrong_key else self.key
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.node['api_port']}" + path,
            data=b"" if method == "POST" else None,
            headers={"X-API-Key": key, "Accept": "application/json"},
            method=method,
        )
        try:
            try:
                response = self.opener.open(request, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise BenchError("Bench API response exceeds its size limit")
                text = body.decode("utf-8")
                content_type = response.headers.get_content_type()
                value = json.loads(text) if content_type == "application/json" else text
                return {
                    "status_code": response.code,
                    "content_type": content_type,
                    "body": value,
                }
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BenchError("Bench API is not reachable within its timeout") from None

    def get(self, path: str):
        result = self.request(path)
        if result["status_code"] != 200:
            raise BenchError("Bench read did not return HTTP 200")
        return result["body"]


def stop(state: dict | None = None) -> dict:
    state = state or read_state()
    stopped = []
    for node in state["nodes"]:
        if not owned(node):
            continue
        try:
            BenchClient(node).request("/rest/system/shutdown", method="POST")
        except BenchError:
            pass
        deadline = time.monotonic() + 8
        while owned(node) and time.monotonic() < deadline:
            time.sleep(0.1)
        if owned(node):
            expected = node["process"]
            if os.name == "nt":
                windows_process(expected["pid"], expected, terminate=True)
            else:
                descriptor = os.pidfd_open(expected["pid"])
                try:
                    if not owned(node):
                        raise BenchError("Bench process identity changed before termination")
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                finally:
                    os.close(descriptor)
        deadline = time.monotonic() + 5
        while owned(node) and time.monotonic() < deadline:
            time.sleep(0.1)
        if owned(node):
            raise BenchError("An owned bench process did not stop")
        node.pop("process", None)
        stopped.append(node["name"])
    write_json(STATE, state)
    return {
        "stopped": stopped,
        "running": [node["name"] for node in state["nodes"] if owned(node)],
    }


def start() -> dict:
    state = read_state()
    running = [node for node in state["nodes"] if owned(node)]
    if running:
        if len(running) == 2:
            return state
        raise BenchError("Partial bench is already running; stop it before starting again")
    for _, api, sync in NODES:
        for port in (api, sync):
            try:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            except OSError:
                raise BenchError(
                    "A reserved bench port is occupied; no existing process was stopped"
                ) from None
    binary = prepare()
    if release()["version"] not in native(binary, "--version").split():
        raise BenchError("Executable version differs from the pinned release")
    state = configure(binary, state)
    try:
        for node in state["nodes"]:
            with (Path(node["home"]) / "process.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        str(binary),
                        "serve",
                        "--home",
                        node["home"],
                        "--no-browser",
                        "--no-upgrade",
                        "--no-port-probing",
                        "--no-restart",
                        "--log-file=-",
                    ],
                    env=clean_environment(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            node["process"] = process_identity(process.pid)
            if node["process"] is None:
                raise BenchError("Syncthing exited before its process identity was captured")
            write_json(STATE, state)
        deadline = time.monotonic() + 25
        for node in state["nodes"]:
            client = BenchClient(node)
            while time.monotonic() < deadline:
                if not owned(node):
                    raise BenchError("Bench process exited during startup")
                try:
                    system = client.get("/rest/system/status")
                    if system["myID"] != node["device_id"]:
                        raise BenchError("Bench device identity does not match its generated home")
                    break
                except BenchError:
                    time.sleep(0.1)
            else:
                raise BenchError("Bench startup exceeded its readiness deadline")
        return state
    except Exception:
        stop(state)
        raise


def capture(state: dict, export: bool) -> tuple[dict, dict]:
    fixtures, requests, errors = {}, [], {}
    for node in state["nodes"]:
        client = BenchClient(node)
        prefix = node["name"]
        paths = {
            "system-status": "/rest/system/status",
            "system-version": "/rest/system/version",
            "folders": "/rest/config/folders",
            "folder-config": f"/rest/config/folders/{FOLDERS[0]}",
            "folder-runtime": f"/rest/db/status?folder={FOLDERS[0]}",
            "restart-required": "/rest/config/restart-required",
            "events": "/rest/events?since=0&timeout=1",
        }
        session_before = client.get("/rest/system/status")["startTime"]
        for kind, path in paths.items():
            value = client.get(path)
            fixtures[f"{prefix}-{kind}.json"] = sanitize(kind, value, prefix)
            requests.append({"node": prefix, "method": "GET", "path": path, "status_code": 200})
        if client.get("/rest/system/status")["startTime"] != session_before:
            raise BenchError("Source restarted while read fixtures were being captured")
        version = fixtures[f"{prefix}-system-version.json"]["version"]
        if version != release()["version"]:
            raise BenchError("REST version differs from the pinned release")
        auth = client.request("/rest/system/status", wrong_key=True)
        missing = client.request("/rest/config/folders/scc-a1-missing")
        if auth["status_code"] not in {401, 403} or missing["status_code"] != 404:
            raise BenchError("Authentication or missing-folder response needs investigation")
        fixtures[f"{prefix}-auth-error.json"] = auth
        fixtures[f"{prefix}-folder-not-found.json"] = missing
        errors[prefix] = {
            "auth": auth["status_code"],
            "missing_folder": missing["status_code"],
        }
    metadata = release()
    manifest = {
        "captured_at": utc_now(),
        "version": metadata["version"],
        "release_url": metadata["release_url"],
        "archive_sha256": metadata["assets"][platform_name()]["sha256"],
        "requests": requests,
        "negative_reads": errors,
        "redactions": [
            "Only allowlisted fields retained",
            "Folder paths replaced by /DEMO",
            "Event payloads reduced",
            "Device IDs belong only to fresh demo nodes",
        ],
        "config_changes": "Initial offline XML of owned homes only; paused PATCH not performed",
    }
    forbidden = [str(ROOT.resolve()), str(ROOT.resolve()).replace("\\", "/")]
    for node in state["nodes"]:
        forbidden.extend([BenchClient(node).key, node["home"], node["home"].replace("\\", "/")])
    assert_public(fixtures, forbidden)
    if export:
        publish(
            ROOT / "tests/contract/syncthing_adapter/fixtures" / metadata["version"],
            fixtures,
            manifest,
            forbidden,
        )
    return fixtures, manifest


def verify(export: bool) -> dict:
    if any(owned(node) for node in read_state()["nodes"]):
        raise BenchError("verify owns its lifecycle; stop the existing bench first")
    report = {"started_at": utc_now(), "stage": "A1.1", "paused_patch_performed": False}
    try:
        state = start()
        sender, receiver = state["nodes"]
        for node in state["nodes"]:
            client = BenchClient(node)
            options = client.get("/rest/config/options")
            for flag in (
                "globalAnnounceEnabled",
                "localAnnounceEnabled",
                "relaysEnabled",
                "natEnabled",
            ):
                if options.get(flag) is not False:
                    raise BenchError("A public network feature is still enabled in the bench")
            if options.get("listenAddresses") != [f"tcp://127.0.0.1:{node['sync_port']}"]:
                raise BenchError(
                    "Synchronization listener is not bound to the private loopback port"
                )
            if client.get("/rest/config/restart-required").get("requiresRestart") is not False:
                raise BenchError("Initial configuration is not fully applied")
        payload = f"SCC A1.1 disposable probe {uuid4()}\n".encode()
        source = Path(sender["home"]) / "data" / FOLDERS[0] / "a11-probe.txt"
        destination = Path(receiver["home"]) / "data" / FOLDERS[0] / source.name
        source.write_bytes(payload)
        scan = BenchClient(sender).request(f"/rest/db/scan?folder={FOLDERS[0]}", method="POST")
        if scan["status_code"] != 200:
            raise BenchError("Initial probe scan was not accepted")
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if destination.exists() and destination.read_bytes() == payload:
                break
            time.sleep(0.2)
        else:
            raise BenchError("Disposable file did not synchronize before its deadline")
        _, manifest = capture(state, export)
        report.update(
            result="passed",
            version=release()["version"],
            nodes_ready=2,
            synchronized_sha256=hashlib.sha256(payload).hexdigest(),
            captured_responses=len(manifest["requests"]) + 4,
            negative_reads=manifest["negative_reads"],
            fixtures_exported=export,
            network_isolation_verified=True,
            initial_configuration="offline owned homes",
            runtime_actions=[
                "write one disposable file",
                "POST db/scan",
                "GET API probes",
            ],
        )
        return report
    except BenchError:
        report["result"] = "failed"
        raise
    finally:
        report["cleanup"] = stop()
        report["completed_at"] = utc_now()
        write_json(RUNTIME / "verification.json", report)
        if export and report.get("result") == "passed":
            evidence = ROOT / "tests/integration/syncthing_adapter/evidence-v2.1.7.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(assert_public(report, [str(ROOT.resolve())]), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "start", "stop", "status", "capture", "verify")
    )
    parser.add_argument("--export-fixtures", action="store_true")
    args = parser.parse_args()
    with operation_lock():
        if args.command == "prepare":
            prepare()
            result = {"version": release()["version"], "archive_sha256": "verified"}
        elif args.command == "start":
            state = start()
            result = {"running": [node["name"] for node in state["nodes"] if owned(node)]}
        elif args.command == "stop":
            result = stop()
        elif args.command == "status":
            result = {"running": [node["name"] for node in read_state()["nodes"] if owned(node)]}
        elif args.command == "capture":
            state = read_state()
            if len(state["nodes"]) != 2 or not all(owned(node) for node in state["nodes"]):
                raise BenchError("Both owned nodes must be running before capture")
            _, result = capture(state, args.export_fixtures)
        else:
            result = verify(args.export_fixtures)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "result": "failed",
                    "error": str(exc) if isinstance(exc, BenchError) else "Bench operation failed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1) from None
