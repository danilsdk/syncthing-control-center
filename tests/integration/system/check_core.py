"""Real A4.2 check in a uniquely owned Compose project; no API/SQL/source doubles."""

import argparse
import http.cookiejar
import importlib.util
import json
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from check_transfer import Fixture, TransferError, verify

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from app.contracts import dto  # noqa: E402

spec = importlib.util.spec_from_file_location("core_stack", ROOT / "scripts/dev_stack.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def require(value, message):
    if not value:
        raise tools.StackError(message)


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Api:
    def __init__(self, port, forbidden):
        self.url = f"http://127.0.0.1:{port}"
        self.origin = self.url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect(),
        )
        self.forbidden, self.tokens = forbidden, []

    def request(self, method, path, body=None, *, headers=None, expected=200, model=None):
        request = urllib.request.Request(
            self.url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(4 * 1024 * 1024 + 1)
            require(len(raw) <= 4 * 1024 * 1024, "Response exceeds size bound")
            require(
                not any(value.encode() in raw for value in self.forbidden),
                "Private value appeared in a response",
            )
            require(response.code == expected, "Unexpected HTTP status for " + path)
            value = json.loads(raw) if raw else None
            if path.startswith("/api/"):
                require(response.headers.get("Cache-Control") == "no-store", "Missing no-store")
                require(bool(response.headers.get("X-Request-ID")), "Missing request ID")
                if expected >= 400:
                    dto.Error.model_validate(value)
                    require(
                        value["error"]["request_id"] == response.headers["X-Request-ID"],
                        "Error request ID differs",
                    )
            if model:
                model.model_validate(value)
            return value

    def login(self, password):
        value = self.request(
            "POST",
            "/api/auth/login",
            {"login": "admin", "password": password},
            headers={"Origin": self.origin},
            model=dto.Session,
        )
        cookie = next(item for item in self.jar if item.name == "scc_session")
        require(cookie.has_nonstandard_attr("HttpOnly"), "Cookie is not HttpOnly")
        self.tokens.extend([cookie.value, value["csrf_token"]])
        return {"Origin": self.origin, "X-CSRF-Token": value["csrf_token"]}

    def ready(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/health/ready") == {"status": "ok"}:
                    return
            except (OSError, tools.StackError):
                pass
            time.sleep(0.2)
        raise tools.StackError("Readiness deadline exceeded")


def run(args, report):
    project = args.project or "scc-a4-test-" + uuid4().hex[:12]
    require(project.startswith("scc-a4-test-"), "Core check requires a test project")
    report["project"] = project
    if args.project:
        port = tools.load_state(project)["port"]
    else:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
    tools.prepare(project, port)
    stack = tools.Stack(project, docker=args.docker, context=args.context)
    private = stack.directory / "private"
    connections = json.loads((private / "connections.json").read_text())
    password = secrets.token_urlsafe(32)
    forbidden = [
        password,
        (private / "database-password").read_text().strip(),
        (private / "session-secret").read_text().strip(),
        str(private),
        *[value["api_key"] for value in connections.values()],
    ]
    api = Api(port, forbidden)
    started = False

    def passed(name, **facts):
        report["phase"] = name
        report["checks"].append({"name": name, "at": utc(), **facts})
        print("PASS: " + name, flush=True)

    def collect():
        result = stack.compose(
            "exec", "-T", "api", "python", "/opt/deploy/entrypoint.py", "collect-once"
        )
        value = json.loads(result.stdout)
        require(
            len(value) == 2
            and all(
                item["events"]["outcome"] == "stored"
                and item["observations"]["outcome"] == "stored"
                for item in value
            ),
            "Real container collection did not store both nodes",
        )

    try:
        stack.preflight()
        require(not any(stack.inventory().values()), "Live check requires a new empty project")
        report["docker"] = (
            stack.run("version", "--format", "{{.Server.Version}}").stdout.decode().strip()
        )
        report["compose"] = stack.run("compose", "version", "--short").stdout.decode().strip()
        passed("linux_engine_and_empty_owned_project")
        report["phase"] = "build"
        stack.build()
        report["backend_image_id"] = (
            stack.run("image", "inspect", f"scc-a4-backend:{project}", "--format", "{{.Id}}")
            .stdout.decode()
            .strip()
        )
        passed("pinned_backend_image_built")
        started = True
        report["phase"] = "core_startup"
        stack.up(build=False)
        api.ready()
        for path in ("/api/nodes", "/api/events"):
            error = api.request("GET", path, expected=503)
            require(error["error"]["code"] == "auth_not_configured", "Unexpected bootstrap state")
        api.request(
            "POST",
            "/api/auth/login",
            {"login": "admin", "password": password},
            headers={"Origin": api.origin},
            expected=503,
        )
        passed("clean_core_migration_readiness_and_no_automatic_administrator")

        bootstrap = stack.compose(
            "exec",
            "-T",
            "api",
            "python",
            "/opt/deploy/entrypoint.py",
            "admin-create",
            "--login",
            "admin",
            input=(password + "\n" + password + "\n").encode(),
        )
        require(b"Administrator created" in bootstrap.stdout, "Real administrator CLI failed")
        require(
            not any(value.encode() in bootstrap.stdout + bootstrap.stderr for value in forbidden),
            "Bootstrap printed a private value",
        )
        api.request("GET", "/api/nodes", expected=401)
        api.request("GET", "/api/events", expected=401)
        headers = api.login(password)
        api.request(
            "POST",
            "/api/nodes",
            {"connection_ref": "demo-a", "display_name": "A"},
            headers={"Origin": api.origin},
            expected=403,
        )
        registered = [
            api.request(
                "POST",
                "/api/nodes",
                {"connection_ref": ref, "display_name": ref},
                headers=headers,
                expected=201,
                model=dto.Node,
            )
            for ref in ("demo-a", "demo-b")
        ]
        ids = [row["id"] for row in registered]

        def mappings():
            return [
                {
                    row["syncthing_folder_id"]: row["id"]
                    for row in api.request(
                        "GET", f"/api/nodes/{identifier}/folders", model=dto.FolderPage
                    )["items"]
                }
                for identifier in ids
            ]

        folders = mappings()
        require(
            all(set(value) == {"scc-a4-shared", "scc-a4-control"} for value in folders)
            and set(folders[0].values()).isdisjoint(folders[1].values()),
            "Wrong folder mapping",
        )
        passed("real_cli_scrypt_login_csrf_and_two_sources_four_folder_mappings")
        require(
            api.request("GET", "/api/snapshots")["total"] == 0,
            "A background collector was started implicitly",
        )
        collect()
        before_events = api.request("GET", "/api/events?limit=100", model=dto.EventPage)
        before_probes = api.request("GET", "/api/snapshots?scope=node", model=dto.SnapshotPage)
        require(
            before_events["total"] > 0 and before_probes["total"] == 2,
            "Container history or logical node probe count is incorrect",
        )
        require(
            api.request("GET", "/api/snapshots?scope=folder")["total"] == 8,
            "Config and runtime snapshots were not separate",
        )
        passed(
            "real_A3_manual_pass_and_guarded_history_without_scheduler",
            events=before_events["total"],
            node_probes=before_probes["total"],
        )

        stack.compose("restart", "api", timeout=45)
        api.ready()
        api.request("GET", "/api/auth/me", model=dto.Session)
        require(mappings() == folders, "API restart changed folder identities")
        require(
            api.request("GET", "/api/events?limit=100") == before_events,
            "API restart changed persisted history",
        )
        passed("api_restart_preserves_session_registry_and_history")

        stack.down()
        require(len(stack.inventory()["volume"]) == 3, "Ordinary down removed persistent volumes")
        stack.up(build=False)
        api.ready()
        require(
            mappings() == folders and api.request("GET", "/api/events?limit=100") == before_events,
            "Recreated containers lost UUID or history",
        )
        api.request("GET", "/api/auth/me", model=dto.Session)
        collect()
        require(
            api.request("GET", "/api/snapshots?scope=node")["total"] == 4,
            "Recreated source probes are missing",
        )
        passed("down_up_preserves_database_demo_identity_and_source_reconciliation")

        report["phase"] = "T02_T04_transfer"
        report["transfer"] = {}
        verify(Fixture(stack, ids), passed, report["transfer"])

        duplicate = stack.compose(
            "exec",
            "-T",
            "api",
            "python",
            "/opt/deploy/entrypoint.py",
            "admin-create",
            "--login",
            "admin",
            check=False,
            input=(password + "\n" + password + "\n").encode(),
        )
        require(duplicate.returncode != 0, "Administrator bootstrap silently overwrote a user")
        api.login(password)
        passed("explicit_bootstrap_cannot_overwrite_administrator")

        inventory = stack.assert_owned()
        for container in inventory["container"]:
            configured = container["HostConfig"].get("PortBindings") or {}
            effective = container["NetworkSettings"].get("Ports") or {}
            for binding in [*configured.values(), *effective.values()]:
                require(
                    all(item["HostIp"] == "127.0.0.1" for item in (binding or [])),
                    "Non-loopback host port",
                )
            require(
                not any(value in "\n".join(container["Config"]["Env"]) for value in forbidden),
                "A private value is in Docker container configuration",
            )
            if container["Config"]["Labels"]["com.docker.compose.service"] != "api":
                require(
                    not configured and not any(effective.values()),
                    "Private service has a host port",
                )
            else:
                require(bool(effective.get("8000/tcp")), "API host port was not published")
        logs = stack.compose("logs", "--no-color", timeout=30).stdout
        require(
            not any(value.encode() in logs for value in [*forbidden, *api.tokens]),
            "A private value appeared in container logs",
        )
        passed("file_secrets_private_ports_and_container_logs")
        report["result"] = "passed"
    finally:
        if report["result"] != "passed":
            diagnostic = stack.directory / "last-command.log"
            content = diagnostic.read_bytes() if diagnostic.is_file() else b""
            if started:
                try:
                    logs = stack.compose(
                        "logs", "--no-color", "--tail", "100", check=False, timeout=20
                    )
                    content += b"\nContainer logs:\n" + logs.stdout + logs.stderr
                except tools.StackError:
                    content += b"\nContainer diagnostics unavailable\n"
            if content:
                content = content[-128 * 1024 :]
                for value in [*forbidden, *api.tokens]:
                    content = content.replace(value.encode(), b"[REDACTED]")
                args.report.parent.mkdir(parents=True, exist_ok=True)
                path = args.report.with_suffix(".diagnostics.log")
                path.write_bytes(content)
                report["diagnostics"] = str(path)
        if started:
            stack.down(delete_data=True)
        remaining = stack.inventory()
        require(not any(remaining.values()), "Owned Docker resources remain after cleanup")
        stack.remove_test_runtime()
        report["cleanup"] = {
            "containers": 0,
            "volumes": 0,
            "networks": 0,
            "private_runtime_exists": stack.directory.exists(),
            "build_cache_and_image_retained": True,
        }
        require(
            not any(value.encode() in json.dumps(report).encode() for value in forbidden),
            "Private value in public report",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--docker")
    parser.add_argument("--context")
    parser.add_argument("--project", help="Existing owned test runtime prepared by the CI runner")
    args = parser.parse_args()
    report = {"stage": "A4.2b", "started_at": utc(), "result": "failed", "checks": []}
    try:
        run(args, report)
    except Exception as error:
        report["result"] = "failed"
        report["error_type"] = type(error).__name__
        if isinstance(error, (tools.StackError, TransferError)):
            report["error"] = str(error)
        print("Core check failed; no private diagnostics were printed", flush=True)
    finally:
        report["completed_at"] = utc()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
