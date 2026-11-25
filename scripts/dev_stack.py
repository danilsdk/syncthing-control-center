"""Own a private A4 core Compose project; never use another project's data."""

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from functools import cached_property
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy/compose/runtime"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PROJECT = re.compile(r"scc-a4-(?:dev|test-[a-z0-9]{8,20})")
DEFAULT_UI_PORT = 8080


class StackError(Exception):
    """Only fixed operational messages; never include private command output."""


ENGINE_PLATFORM_FORMAT = "{{.OSType}}/{{.Architecture}}"


def linux_platform(engine):
    platform = {
        b"linux/x86_64": "linux/amd64",
        b"linux/amd64": "linux/amd64",
        b"linux/aarch64": "linux/arm64",
        b"linux/arm64": "linux/arm64",
    }.get(engine.strip())
    if platform is None:
        raise StackError("The core requires a Linux Docker Engine on amd64 or arm64")
    return platform


def docker_binary(explicit=None):
    if explicit:
        return explicit
    found = shutil.which("docker")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs/DockerDesktop/resources/bin/docker.exe",
        Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise StackError("Docker CLI was not found")


def project_directory(project):
    if not PROJECT.fullmatch(project):
        raise StackError("Invalid owned project name")
    directory = RUNTIME / project
    if directory.is_symlink() or directory.resolve().parent != RUNTIME.resolve():
        raise StackError("Project path is outside the owned runtime")
    return directory


def free_test_ports():
    with socket.socket() as api, socket.socket() as ui:
        for listener in (api, ui):
            listener.bind(("127.0.0.1", 0))
        return api.getsockname()[1], ui.getsockname()[1]


def default_ui_port(api_port):
    return 8081 if api_port == DEFAULT_UI_PORT else DEFAULT_UI_PORT


def prepare(project, port, ui_port=None):
    directory = project_directory(project)
    if directory.exists():
        state = load_state(project)
        if state["port"] != port or (ui_port is not None and state["ui_port"] != ui_port):
            raise StackError("Existing project uses different API or UI ports")
        return state
    if ui_port is None:
        ui_port = default_ui_port(port)
    if not all(1024 <= value <= 65535 for value in (port, ui_port)) or port == ui_port:
        raise StackError("API and UI require distinct loopback ports between 1024 and 65535")
    directory.mkdir(parents=True, mode=0o700)
    private = directory / "private"
    private.mkdir(mode=0o700)
    owner = {"schema": 1, "project": project, "owner": str(uuid4())}
    values = {
        "database-password": secrets.token_urlsafe(36),
        "session-secret": secrets.token_urlsafe(48),
        "connections.json": json.dumps(
            {
                f"demo-{letter}": {
                    "display_name": f"Demo {letter.upper()}",
                    "base_url": f"http://syncthing-{letter}:8384",
                    "api_key": secrets.token_urlsafe(36),
                }
                for letter in "ab"
            }
        ),
        "owner.json": json.dumps(owner),
    }
    for name, value in values.items():
        path = private / name
        # Bash file readers strip LF, while CRLF would add CR to the database password.
        path.write_text(value + "\n", encoding="utf-8", newline="\n")
        # Parent is private (0700); mounted files must be readable by nonroot container UIDs.
        path.chmod(0o444)
    state = {**owner, "root": str(ROOT.resolve()), "port": port, "ui_port": ui_port}
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def load_state(project):
    directory = project_directory(project)
    path = directory / "state.json"
    if not path.is_file() or path.stat().st_size > 4096:
        raise StackError("Run prepare for this project first")
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        state.get("schema") != 1
        or state.get("project") != project
        or state.get("root") != str(ROOT.resolve())
    ):
        raise StackError("Runtime belongs to another checkout or project")
    UUID(state["owner"])
    owner_path = directory / "private/owner.json"
    if json.loads(owner_path.read_text(encoding="utf-8")) != {
        key: state[key] for key in ("schema", "project", "owner")
    }:
        raise StackError("Private owner marker differs")
    # Older A4 runtimes record only the API port. Reuse the same volumes and keys.
    state.setdefault("ui_port", default_ui_port(state["port"]))
    return state


class Stack:
    def __init__(self, project, *, docker=None, context=None):
        self.state = load_state(project)
        self.project, self.directory = project, project_directory(project)
        executable = docker_binary(docker)
        self.docker = [executable]
        context = context or ("desktop-linux" if os.name == "nt" else None)
        if context:
            self.docker += ["--context", context]
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("SCC_", "COMPOSE_"))
        }
        self.env["PATH"] = str(Path(executable).parent) + os.pathsep + self.env.get("PATH", "")
        self.env.update(
            SCC_PROJECT=project,
            SCC_OWNER=self.state["owner"],
            SCC_PRIVATE_DIR=(self.directory / "private").resolve().as_posix(),
            SCC_API_PORT=str(self.state["port"]),
            SCC_UI_PORT=str(self.state["ui_port"]),
        )
        self.compose_args = [
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(ROOT / "deploy/compose/defaults.env"),
            "--file",
            str(ROOT / "compose.yaml"),
        ]

    def run(self, *arguments, input=None, timeout=120, check=True):
        try:
            result = subprocess.run(
                [*self.docker, *arguments],
                cwd=ROOT,
                env=self.env,
                input=input,
                capture_output=True,
                timeout=timeout,
                creationflags=HIDDEN,
            )
        except subprocess.TimeoutExpired as error:
            (self.directory / "last-command.log").write_bytes(
                (error.stdout or b"") + (error.stderr or b"")
            )
            raise StackError("Docker operation could not finish within its deadline") from None
        except OSError:
            raise StackError("Docker operation could not finish within its deadline") from None
        if check and result.returncode:
            (self.directory / "last-command.log").write_bytes(result.stdout + result.stderr)
            raise StackError("Docker operation failed; private diagnostics retained in runtime")
        return result

    @cached_property
    def platform(self):
        engine = self.run("info", "--format", ENGINE_PLATFORM_FORMAT, timeout=25)
        return linux_platform(engine.stdout)

    def compose(self, *arguments, **kwargs):
        self.env["SCC_DOCKER_PLATFORM"] = self.platform
        return self.run(*self.compose_args, *arguments, **kwargs)

    def preflight(self):
        self.run("compose", "version", "--short", timeout=15)
        self.assert_owned()
        self.compose("config", "--quiet", timeout=20)

    def inventory(self):
        found = {}
        for kind, command in [
            ("container", ["ps", "-aq"]),
            ("volume", ["volume", "ls", "-q"]),
            ("network", ["network", "ls", "-q"]),
        ]:
            raw = self.run(*command, "--filter", f"label=com.docker.compose.project={self.project}")
            identifiers = raw.stdout.decode().split()
            if identifiers:
                inspected = self.run(kind, "inspect", *identifiers)
                found[kind] = json.loads(inspected.stdout)
            else:
                found[kind] = []
        return found

    def assert_owned(self):
        found = self.inventory()
        for kind, entries in found.items():
            for entry in entries:
                labels = (
                    entry["Config"].get("Labels", {}) if kind == "container" else entry["Labels"]
                )
                if (
                    labels.get("io.scc.owner") != self.state["owner"]
                    or labels.get("io.scc.stage") != "a4-core"
                    or labels.get("com.docker.compose.project") != self.project
                ):
                    raise StackError(
                        "Foreign resources share this project name; nothing was changed"
                    )
        return found

    def build(self):
        self.preflight()
        print("Building the pinned backend and UI images", flush=True)
        result = self.compose("build", "api", "ui", timeout=600)
        (self.directory / "build.log").write_bytes(result.stdout + result.stderr)

    def up(self, *, build=True, include_worker=True):
        self.preflight()
        if build:
            self.build()
        self.assert_owned()
        self.compose("run", "--rm", "--no-deps", "-T", "init-demo", timeout=90)
        services = ("ui", "worker") if include_worker else ("ui",)
        self.compose("up", "-d", "--wait", "--wait-timeout", "150", *services, timeout=180)
        self.wait_ready("port", "API")
        self.wait_ready("ui_port", "UI proxy")
        print(f"Owned UI is ready at {self.ui_url}", flush=True)

    @property
    def ui_url(self):
        return f"http://127.0.0.1:{self.state['ui_port']}"

    def wait_ready(self, port_key, service):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 20
        while True:
            try:
                with opener.open(
                    f"http://127.0.0.1:{self.state[port_key]}/health/ready", timeout=2
                ) as response:
                    if response.status == 200 and json.loads(response.read(4097)) == {
                        "status": "ok"
                    }:
                        return
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise StackError(f"Published loopback {service} did not become ready")
            time.sleep(0.2)

    def down(self, *, delete_data=False):
        self.assert_owned()
        options = ["down", "--remove-orphans", "--timeout", "30"]
        if delete_data:
            options.append("--volumes")
        self.compose(*options, timeout=90)

    def remove_test_runtime(self):
        if not self.project.startswith("scc-a4-test-"):
            raise StackError("Only an owned test runtime can be removed automatically")
        load_state(self.project)
        if any(self.inventory().values()):
            raise StackError("Resources still exist; runtime retained")
        directory = project_directory(self.project).resolve()
        if directory.parent != RUNTIME.resolve():
            raise StackError("Refusing cleanup outside the runtime")
        # The resolved workspace target and matching owner were checked before recursion.
        for path in (directory / "private").iterdir():
            path.chmod(0o600)
        shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "check",
            "build",
            "up",
            "status",
            "down",
            "admin-create",
            "collect-once",
        ),
    )
    parser.add_argument("--project", default="scc-a4-dev")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int)
    parser.add_argument("--docker")
    parser.add_argument("--context")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--delete-data", action="store_true")
    parser.add_argument("--confirm-project")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.project, args.port, args.ui_port)
        print("Owned private settings prepared; secrets were not printed")
        return
    stack = Stack(args.project, docker=args.docker, context=args.context)
    if args.command == "check":
        stack.preflight()
        print("Linux Engine, Compose, private settings and ownership checks passed")
    elif args.command == "build":
        stack.build()
    elif args.command == "up":
        stack.up()
    elif args.command == "status":
        stack.assert_owned()
        result = stack.compose("ps", "--format", "json")
        print(result.stdout.decode())
    elif args.command == "down":
        if args.delete_data and args.confirm_project != args.project:
            raise StackError("Deleting data requires --confirm-project with the exact project name")
        stack.down(delete_data=args.delete_data)
        print("Owned core stopped; data " + ("deleted" if args.delete_data else "preserved"))
    elif args.command == "collect-once":
        stack.assert_owned()
        print(
            stack.compose(
                "exec", "-T", "api", "python", "/opt/deploy/entrypoint.py", "collect-once"
            ).stdout.decode()
        )
    else:
        stack.assert_owned()
        # Password is entered directly through getpass, never in arguments or our logs.
        result = subprocess.run(
            [
                *stack.docker,
                *stack.compose_args,
                "exec",
                "api",
                "python",
                "/opt/deploy/entrypoint.py",
                "admin-create",
                "--login",
                args.login,
            ],
            cwd=ROOT,
            env=stack.env,
        )
        if result.returncode:
            raise StackError("Administrator was not created")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(
            str(error) if isinstance(error, StackError) else "Core command failed or cancelled",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
