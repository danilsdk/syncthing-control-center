"""Own a private A4 core Compose project; never use another project's data."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy/compose/runtime"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PROJECT = re.compile(r"scc-a4-(?:dev|test-[a-z0-9]{8,20})")


class StackError(Exception):
    """Only fixed operational messages; never include private command output."""


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


def prepare(project, port):
    directory = project_directory(project)
    if not 1024 <= port <= 65535:
        raise StackError("Invalid loopback API port")
    if directory.exists():
        state = load_state(project)
        if state["port"] != port:
            raise StackError("Existing project uses another API port")
        return state
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
        path.write_text(value + "\n", encoding="utf-8")
        # Parent is private (0700); mounted files must be readable by nonroot container UIDs.
        path.chmod(0o444)
    state = {**owner, "root": str(ROOT.resolve()), "port": port}
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
        except (OSError, subprocess.TimeoutExpired):
            raise StackError("Docker operation could not finish within its deadline") from None
        if check and result.returncode:
            (self.directory / "last-command.log").write_bytes(result.stdout + result.stderr)
            raise StackError("Docker operation failed; private diagnostics retained in runtime")
        return result

    def compose(self, *arguments, **kwargs):
        return self.run(*self.compose_args, *arguments, **kwargs)

    def preflight(self):
        result = self.run("info", "--format", "{{.OSType}}", timeout=25)
        if result.stdout.strip() != b"linux":
            raise StackError("The core requires a running Linux Docker Engine")
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
        print("Building the pinned backend image", flush=True)
        result = self.compose("build", "api", timeout=600)
        (self.directory / "build.log").write_bytes(result.stdout + result.stderr)

    def up(self, *, build=True):
        self.preflight()
        if build:
            self.build()
        self.assert_owned()
        self.compose("run", "--rm", "--no-deps", "-T", "init-demo", timeout=90)
        self.compose("up", "-d", "--wait", "--wait-timeout", "150", "api", timeout=180)
        print(f"Owned core is ready at http://127.0.0.1:{self.state['port']}", flush=True)

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
    parser.add_argument("--docker")
    parser.add_argument("--context")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--delete-data", action="store_true")
    parser.add_argument("--confirm-project")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.project, args.port)
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
