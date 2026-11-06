"""Explicit A2.4: real loopback HTTP, private PostgreSQL and two owned Syncthing nodes."""

import argparse
import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "deploy/syncthing"))

import bench  # noqa: E402
from app.config import Settings  # noqa: E402
from app.contracts import dto  # noqa: E402
from app.contracts.interfaces import AdapterError, NodeConnection  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import AppUser, Folder, ManagedNode  # noqa: E402
from app.registry.admin import create_administrator  # noqa: E402
from app.syncthing_adapter import HttpSyncthingAdapter  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

ORIGIN = "http://localhost:8000"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def require(condition, message):
    if not condition:
        raise bench.BenchError(message)


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise bench.BenchError("Observation deadline exceeded")


class Api:
    def __init__(self, env):
        self.env = env
        self.process = None
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
            bench.NoRedirect(),
        )
        self.log = bench.RUNTIME / "a24-api.log"
        self.log.write_bytes(b"")
        self.forbidden = []
        self.tokens = []

    def start(self, connections):
        require(self.process is None, "API is already owned by this run")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        env = {**self.env, "SCC_CONNECTIONS": json.dumps(connections)}
        with self.log.open("ab") as output:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--no-access-log",
                    "--no-proxy-headers",
                ],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=HIDDEN,
            )

        def ready():
            require(self.process.poll() is None, "Owned API exited before readiness")
            try:
                return self.request("GET", "/health/ready", expected=None)[0] == 200
            except (urllib.error.URLError, TimeoutError):
                return False

        wait_for(ready)

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=15)
            self.process = None

    def request(self, method, path, body=None, *, headers=None, expected=200, model=None):
        request = urllib.request.Request(
            self.url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(4 * 1024 * 1024 + 1)
            require(len(raw) <= 4 * 1024 * 1024, "API response exceeds size bound")
            require(
                not any(value.encode() in raw for value in self.forbidden),
                "A private value appeared in an API body",
            )
            value = json.loads(raw) if raw else None
            if expected is not None:
                require(response.code == expected, f"Unexpected HTTP status for {method} {path}")
            if path.startswith("/api/"):
                require(response.headers.get("Cache-Control") == "no-store", "Missing no-store")
                request_id = response.headers.get("X-Request-ID")
                require(bool(request_id), "Missing request ID")
                if response.code >= 400:
                    dto.Error.model_validate(value)
                    require(value["error"]["request_id"] == request_id, "Wrong error request ID")
            if model is not None:
                model.model_validate(value)
            return response.code, value

    def login(self, password):
        _, value = self.request(
            "POST",
            "/api/auth/login",
            {"login": "admin", "password": password},
            headers={"Origin": ORIGIN},
            model=dto.Session,
        )
        cookie = next(item for item in self.jar if item.name == "scc_session")
        require(cookie.has_nonstandard_attr("HttpOnly"), "Session cookie is not HttpOnly")
        require(cookie.get_nonstandard_attr("SameSite", "").lower() == "lax", "Wrong SameSite")
        self.tokens.extend([cookie.value, value["csrf_token"]])
        return {"Origin": ORIGIN, "X-CSRF-Token": value["csrf_token"]}


def checkpoint(database, identifier):
    with database.session() as session:
        row = session.get(ManagedNode, UUID(identifier))
        return (
            row.syncthing_device_id,
            row.source_start_time_raw,
            row.source_session_id,
            row.source_stream_anchor,
            row.last_event_id,
        )


def run(report):
    url = make_url(os.environ.get("SCC_DATABASE_URL", ""))
    require(
        os.environ.get("SCC_AUTH_TEST_PRIVATE_CLUSTER") == "a22-private-cluster"
        and url.host == "127.0.0.1"
        and url.database == "scc_a22_tests"
        and url.port not in {None, 5432},
        "Use the owned private PostgreSQL runner",
    )
    require(
        not any(bench.owned(n) for n in bench.read_state()["nodes"]),
        "Existing bench is running; its lifecycle was not touched",
    )
    database = Database(Settings())
    database.check_ready()
    with database.session() as session:
        require(
            session.scalar(select(func.count()).select_from(ManagedNode)) == 0,
            "Live check requires an empty private database",
        )
    password, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    admin_id = create_administrator(database, "admin", password)
    with database.session() as session:
        password_hash = session.get(AppUser, admin_id).password_hash
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SCC_")}
    env.update(
        SCC_DATABASE_URL=url.render_as_string(hide_password=False),
        SCC_SESSION_SECRET=secret,
        SCC_OBSERVATION_INTERVAL_SECONDS="1",
        PYTHONPATH=str(ROOT / "backend"),
        PYTHONIOENCODING="utf-8",
    )
    api = Api(env)
    control = None
    started = False

    def passed(name):
        report["checks"].append(name)
        print(f"PASS: {name}", flush=True)

    try:
        started = True
        state = bench.start()
        nodes = state["nodes"]
        probes = [bench.BenchClient(node) for node in nodes]
        connections = {
            f"demo-{letter}": {
                "display_name": f"Demo {letter.upper()}",
                "base_url": f"http://127.0.0.1:{node['api_port']}",
                "api_key": probe.key,
            }
            for letter, node, probe in zip("ab", nodes, probes, strict=True)
        }
        connections["bad-key"] = {**connections["demo-a"], "api_key": secrets.token_urlsafe(32)}
        api.forbidden = [
            password,
            password_hash,
            secret,
            url.password,
            *[p.key for p in probes],
            connections["bad-key"]["api_key"],
            *[c["base_url"] for c in connections.values()],
            *[node["home"] for node in nodes],
        ]
        control = HttpSyncthingAdapter(
            NodeConnection(
                node_id=uuid4(), base_url=connections["demo-a"]["base_url"], api_key=probes[0].key
            ),
            expected_device_id=nodes[0]["device_id"],
        )
        initial_configs = [probe.get("/rest/config/folders") for probe in probes]
        control.set_paused(bench.FOLDERS[0], True)
        wait_for(lambda: control.read_folder(bench.FOLDERS[0]).paused is True)
        api.start(connections)
        passed("owned_services_ready")

        a_path = f"/api/nodes/{uuid4()}"
        for method, path, body in [
            ("GET", "/api/connections", None),
            ("GET", "/api/nodes", None),
            ("GET", "/api/auth/me", None),
            ("GET", a_path, None),
            ("GET", a_path + "/folders", None),
            ("POST", "/api/nodes", {"connection_ref": "demo-a", "display_name": "A"}),
            ("PATCH", a_path, {"enabled": False}),
            ("POST", "/api/auth/logout", None),
        ]:
            api.request(method, path, body, expected=401)
        api.request(
            "POST", "/api/auth/login", {"login": "admin", "password": password}, expected=403
        )
        api.request(
            "POST",
            "/api/auth/login",
            {"login": "admin", "password": "wrong"},
            headers={"Origin": ORIGIN},
            expected=401,
        )
        headers = api.login(password)
        me = api.request("GET", "/api/auth/me", model=dto.Session)[1]
        require(headers["X-CSRF-Token"] == me["csrf_token"], "CSRF changed during session read")
        passed("T12_session_and_all_registry_guards")

        payload = {"connection_ref": "demo-a", "display_name": "Alpha"}
        api.request("POST", "/api/nodes", payload, headers={"Origin": ORIGIN}, expected=403)
        api.request(
            "POST",
            "/api/nodes",
            payload,
            headers={**headers, "Origin": "https://forbidden.invalid"},
            expected=403,
        )
        api.request(
            "POST",
            "/api/nodes",
            {**payload, "connection_ref": "missing"},
            headers=headers,
            expected=422,
        )
        api.request(
            "POST",
            "/api/nodes",
            {**payload, "api_key": "browser-input"},
            headers=headers,
            expected=422,
        )
        failed = api.request(
            "POST",
            "/api/nodes",
            {**payload, "connection_ref": "bad-key"},
            headers=headers,
            expected=502,
        )[1]
        require(failed["error"]["code"] == "upstream_auth_error", "Wrong key misclassified")
        require(api.request("GET", "/api/nodes")[1]["total"] == 0, "Partial registration")
        passed("origin_csrf_allowlist_and_atomic_wrong_key_failure")

        registered = [
            api.request(
                "POST",
                "/api/nodes",
                {"connection_ref": ref, "display_name": name},
                headers=headers,
                expected=201,
                model=dto.Node,
            )[1]
            for ref, name in [("demo-a", "Alpha"), ("demo-b", "Beta")]
        ]
        a, b = [item["id"] for item in registered]
        a_path, b_path = f"/api/nodes/{a}", f"/api/nodes/{b}"
        pages = [
            api.request("GET", path + "/folders", model=dto.FolderPage)[1]
            for path in (a_path, b_path)
        ]
        folder_ids = [{f["syncthing_folder_id"]: f["id"] for f in p["items"]} for p in pages]
        require(all(set(ids) == set(bench.FOLDERS) for ids in folder_ids), "Wrong folder mapping")
        require(
            all(folder_ids[0][key] != folder_ids[1][key] for key in bench.FOLDERS),
            "Folder UUIDs were shared between nodes",
        )
        require(
            [item["syncthing_device_id"] for item in registered]
            == [node["device_id"] for node in nodes],
            "Wrong registered identity",
        )
        paused = next(f for f in pages[0]["items"] if f["syncthing_folder_id"] == bench.FOLDERS[0])
        require(
            paused["actual_paused"] is True
            and paused["runtime_state"] is None
            and paused["runtime_observed_at"] is None
            and paused["need_bytes"] is None,
            "Paused unknown runtime was fabricated",
        )
        original_checkpoint = checkpoint(database, a)
        require(
            original_checkpoint[3] is not None and original_checkpoint[4] == 0,
            "Registration did not persist initial proof",
        )
        passed("T01_two_real_nodes_four_distinct_folder_records_and_unknown_runtime")

        api.request("POST", "/api/nodes", payload, headers=headers, expected=409)
        require(
            api.request("GET", "/api/nodes?limit=1&offset=1", model=dto.NodePage)[1]["total"] == 2,
            "Wrong node pagination",
        )
        api.request("GET", "/api/connections?limit=1", model=dto.ConnectionPage)
        api.request("GET", "/api/nodes?limit=0", expected=422)
        api.request("GET", f"/api/nodes/{uuid4()}", expected=404)
        api.request("PATCH", a_path, {"actual_paused": False}, headers=headers, expected=422)
        api.request("PATCH", a_path, {"display_name": "Renamed A"}, headers=headers, model=dto.Node)
        control.set_paused(bench.FOLDERS[0], False)
        wait_for(lambda: control.read_folder(bench.FOLDERS[0]).paused is False)

        def resumed():
            page = api.request("GET", a_path + "/folders", model=dto.FolderPage)[1]
            item = next(f for f in page["items"] if f["syncthing_folder_id"] == bench.FOLDERS[0])
            return item["actual_paused"] is False and item["runtime_state"] is not None

        wait_for(resumed)
        cached = api.request("GET", a_path + "/folders")[1]
        passed("paging_duplicate_patch_and_observed_runtime_recovery")

        bench.stop({**state, "nodes": [nodes[0]]})
        bench.write_json(bench.STATE, state)
        wait_for(lambda: api.request("GET", a_path)[1]["api_status"] == "unreachable")
        outage = api.request("GET", a_path + "/folders")[1]
        for previous in cached["items"]:
            current = next(f for f in outage["items"] if f["id"] == previous["id"])
            require(
                current["is_stale"]
                and all(
                    current[key] == previous[key]
                    for key in (
                        "actual_paused",
                        "config_observed_at",
                        "runtime_state",
                        "runtime_observed_at",
                        "need_bytes",
                        "pull_errors",
                        "present",
                    )
                ),
                "Outage erased a cached value",
            )
        require(api.request("GET", b_path)[1]["api_status"] == "ok", "Peer was affected")
        api.request("PATCH", a_path, {"enabled": False}, headers=headers)
        require(api.request("GET", a_path)[1]["is_stale"], "Disabled node is not stale")
        api.request("PATCH", a_path, {"enabled": True}, headers=headers)
        passed("single_source_outage_retains_observations_and_peer_availability")

        api.stop()
        swapped = {**connections, "demo-a": {**connections["demo-b"]}}
        api.start(swapped)
        wait_for(lambda: api.request("GET", a_path)[1]["api_status"] == "identity_mismatch")
        require(checkpoint(database, a) == original_checkpoint, "Identity mismatch replaced proof")
        api.stop()
        wrong = {
            **connections,
            "demo-a": {**connections["demo-a"], "api_key": connections["bad-key"]["api_key"]},
        }
        # Both real sources are restarted only as an explicit owned lifecycle test.
        bench.stop()

        def ports_free():
            try:
                for _, api_port, sync_port in bench.NODES:
                    for port in (api_port, sync_port):
                        with socket.socket() as probe:
                            probe.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

        wait_for(ports_free, 130)
        state = bench.start()
        api.start(wrong)
        wait_for(lambda: api.request("GET", a_path)[1]["api_status"] == "auth_error")
        require(checkpoint(database, a) == original_checkpoint, "Wrong key replaced proof")
        passed("pinned_identity_and_registered_source_auth_failure")

        api.stop()
        api.start(connections)
        wait_for(lambda: api.request("GET", a_path)[1]["api_status"] == "ok")
        recovered = api.request("GET", a_path + "/folders", model=dto.FolderPage)[1]
        require(
            {f["syncthing_folder_id"]: f["id"] for f in recovered["items"]} == folder_ids[0],
            "Restart changed folder UUIDs",
        )
        require(checkpoint(database, a) == original_checkpoint, "Status reads changed A3 proof")
        require(api.request("GET", a_path)[1]["display_name"] == "Renamed A", "PATCH was lost")
        require(api.request("GET", "/api/nodes")[1]["total"] == 2, "Restart created duplicates")
        # Prove the source actually changed epoch while the canonical A3 proof did not.
        current_source = control.read_system()
        require(
            current_source.source_session_id != original_checkpoint[2], "Source did not restart"
        )
        passed("api_and_source_restart_preserve_registry_and_canonical_checkpoint")

        api.request("POST", "/api/auth/logout", headers=headers, expected=204)
        api.request("GET", "/api/nodes", expected=401)
        api.login(password)
        require(
            api.request("GET", "/api/auth/me")[1]["user"]["id"] == me["user"]["id"],
            "Administrator identity changed",
        )
        final_configs = [
            bench.BenchClient(node).get("/rest/config/folders") for node in state["nodes"]
        ]
        require(final_configs == initial_configs, "Demo folder configuration was not restored")
        with database.session() as session:
            require(
                session.scalar(select(func.count()).select_from(ManagedNode)) == 2
                and session.scalar(select(func.count()).select_from(Folder)) == 4,
                "Wrong persisted registry counts",
            )
        passed("logout_relogin_and_restored_demo_configuration")
        report["result"] = "passed"
    finally:
        api.stop()
        if control is not None:
            try:
                if control.read_folder(bench.FOLDERS[0]).paused:
                    control.set_paused(bench.FOLDERS[0], False)
            except AdapterError:
                pass  # An already stopped source is still cleaned up by its owned lifecycle.
            control.close()
        cleanup = bench.stop() if started else {"running": []}
        database.close()
        report["cleanup"] = {
            "api_running": api.process is not None,
            "syncthing_running": cleanup["running"],
        }
        private = [*api.forbidden, *api.tokens]
        logs = api.log.read_bytes()
        require(not any(value.encode() in logs for value in private), "Private API log value")
        require(
            not any(value.encode() in json.dumps(report).encode() for value in private),
            "Private report value",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=bench.RUNTIME / "a24-verification.json")
    args = parser.parse_args()
    report = {
        "stage": "A2.4",
        "started_at": bench.utc_now(),
        "result": "failed",
        "syncthing_version": bench.release()["version"],
        "checks": [],
    }
    try:
        with bench.operation_lock():
            run(report)
    except Exception as error:
        report["result"] = "failed"
        report["error_type"] = type(error).__name__
        if isinstance(error, bench.BenchError):
            report["error"] = str(error)
        print(
            "Live registry check failed; inspect the current check and private runtime", flush=True
        )
    finally:
        report["completed_at"] = bench.utc_now()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
