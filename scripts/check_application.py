"""Run the application checks locally or in CI, using only owned test resources."""

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_stack", ROOT / "scripts/dev_stack.py")
stack_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack_tools)
TESTS = [
    "tests/unit",
    "tests/contract",
    "tests/integration/auth",
    "tests/integration/registry",
    "tests/integration/collection",
    "tests/integration/system",
]
LINT = [
    "backend/app",
    "tests",
    "scripts/dev_stack.py",
    "scripts/check_application.py",
    "deploy/compose/image",
]


class CheckFailed(Exception):
    """Fixed public messages, without subprocess output or connection strings."""


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def clean_env():
    env = {
        k: v for k, v in os.environ.items() if not k.upper().startswith(("SCC_", "PG", "COMPOSE_"))
    }
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return env


def free_port():
    while True:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port != 5432:
            return port


def junit(source, destination, private_values=()):
    content = source.read_bytes()
    for value in private_values:
        content = content.replace(value.encode(), b"[REDACTED]")
    root = ET.fromstring(content)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "errors", "failures", "skipped")
    }
    destination.write_bytes(content)
    if counts["tests"] == 0 or any(counts[key] for key in ("errors", "failures", "skipped")):
        raise CheckFailed("JUnit contains missing, failed or skipped tests")
    return counts


def prepare(work, reports, docker=None, context=None):
    work, reports = work.resolve(), reports.resolve()
    if (
        work == ROOT
        or ROOT.is_relative_to(work)
        or work.is_relative_to(ROOT)
        or reports.is_relative_to(ROOT)
        or reports.is_relative_to(work)
        or work.is_relative_to(reports)
        or work.exists()
        or reports.exists()
    ):
        raise CheckFailed("Use separate new private and report directories")
    work.mkdir(parents=True, mode=0o700)
    reports.mkdir(parents=True)
    (work / "logs").mkdir(mode=0o700)
    owner = uuid4()
    state = {
        "schema": 1,
        "owner": str(owner),
        "root": str(ROOT.resolve()),
        "work": str(work),
        "reports": str(reports),
        "docker": stack_tools.docker_binary(docker),
        "context": context or ("desktop-linux" if os.name == "nt" else None),
        "core_project": "scc-a4-test-" + owner.hex[:12],
        "core_owner": None,
        "postgres": "scc-a4-ci-pg-" + owner.hex,
    }
    write_json(work / "owner.json", state)
    return state


def load_state(work, reports):
    work, reports = work.resolve(), reports.resolve()
    state = json.loads((work / "owner.json").read_text(encoding="utf-8"))
    owner = UUID(state["owner"])
    if (
        work.is_symlink()
        or state.get("schema") != 1
        or state.get("root") != str(ROOT.resolve())
        or state.get("work") != str(work)
        or state.get("reports") != str(reports)
        or work == ROOT
        or ROOT.is_relative_to(work)
        or work.is_relative_to(ROOT)
        or reports.is_relative_to(ROOT)
        or reports.is_relative_to(work)
        or state.get("postgres") != "scc-a4-ci-pg-" + owner.hex
        or state.get("core_project") != "scc-a4-test-" + owner.hex[:12]
    ):
        raise CheckFailed("Private CI marker does not belong to this checkout and run")
    return state


class Runner:
    def __init__(self, state):
        self.state = state
        self.work, self.reports = Path(state["work"]), Path(state["reports"])
        self.env, self.serial = clean_env(), 0
        self.env["PATH"] = str(Path(state["docker"]).parent) + os.pathsep + self.env.get("PATH", "")
        self.docker = [state["docker"]]
        if state["context"]:
            self.docker += ["--context", state["context"]]

    def command(self, args, *, env=None, timeout=180, check=True, static_output=False):
        self.serial += 1
        logfile = self.work / "logs" / f"command-{self.serial:03}.log"
        try:
            result = subprocess.run(
                args,
                cwd=ROOT,
                env=env or self.env,
                capture_output=True,
                timeout=timeout,
                creationflags=stack_tools.HIDDEN,
            )
        except subprocess.TimeoutExpired as error:
            logfile.write_bytes((error.stdout or b"") + (error.stderr or b""))
            raise CheckFailed("Application command exceeded its deadline") from None
        logfile.write_bytes(result.stdout + result.stderr)
        if static_output:
            # Only pre-runtime source checks are public; live command output stays private.
            print(
                (result.stdout + result.stderr).decode("utf-8", errors="replace"),
                end="",
                flush=True,
            )
        if check and result.returncode:
            raise CheckFailed("Application command failed; private diagnostics retained")
        return result

    def inspect_pg(self):
        result = self.command(
            [*self.docker, "container", "inspect", self.state["postgres"]], check=False, timeout=20
        )
        if result.returncode:
            # Distinguish a missing container from an unavailable daemon.
            self.command([*self.docker, "info", "--format", "{{.OSType}}"], timeout=20)
            return None
        value = json.loads(result.stdout)[0]
        labels = value["Config"].get("Labels") or {}
        if (
            labels.get("io.scc.owner") != self.state["owner"]
            or labels.get("io.scc.stage") != "a4-ci-pg"
        ):
            raise CheckFailed("Foreign PostgreSQL container was preserved")
        return value

    def postgres(self):
        import psycopg
        from sqlalchemy import URL

        if self.inspect_pg() is not None:
            raise CheckFailed("SQL check requires a new PostgreSQL container")
        password = secrets.token_urlsafe(36)
        secret = self.work / "database-password"
        secret.write_text(password + "\n", encoding="utf-8", newline="\n")
        secret.chmod(0o444)
        pins = json.loads((ROOT / "deploy/compose/images.json").read_text())
        image = next(row for row in pins["images"] if row["tag"].startswith("postgres:"))
        port = free_port()
        self.command(
            [
                *self.docker,
                "run",
                "-d",
                "--name",
                self.state["postgres"],
                "--label",
                "io.scc.owner=" + self.state["owner"],
                "--label",
                "io.scc.stage=a4-ci-pg",
                "--platform",
                "linux/amd64",
                "--publish",
                f"127.0.0.1:{port}:5432",
                "--tmpfs",
                "/var/lib/postgresql:rw,size=536870912",
                "--mount",
                f"type=bind,source={secret.as_posix()},target=/run/secrets/db,readonly",
                "--env",
                "POSTGRES_USER=scc_test",
                "--env",
                "POSTGRES_DB=postgres",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/db",
                "--env",
                "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=scram-sha-256",
                image["tag"] + "@" + image["digest"],
            ],
            timeout=180,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                connection = psycopg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="scc_test",
                    password=password,
                    dbname="postgres",
                    autocommit=True,
                    connect_timeout=1,
                )
                break
            except psycopg.Error:
                if time.monotonic() >= deadline:
                    raise CheckFailed("Private PostgreSQL did not become ready") from None
                time.sleep(0.2)
        env = self.env.copy()
        suites = [
            ("scc_a03_tests", "SCC_TEST", "a03-private-cluster"),
            ("scc_a22_tests", "SCC_AUTH_TEST", "a22-private-cluster"),
            ("scc_a32_tests", "SCC_COLLECTION_TEST", "a32-private-cluster"),
        ]
        with connection:
            version = connection.info.server_version
            for database, prefix, marker in suites:
                connection.execute(
                    psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database))
                )
                url = URL.create(
                    "postgresql+psycopg",
                    username="scc_test",
                    password=password,
                    host="127.0.0.1",
                    port=port,
                    database=database,
                ).render_as_string(hide_password=False)
                env[prefix + "_DATABASE_URL"], env[prefix + "_PRIVATE_CLUSTER"] = url, marker
                for action in [("upgrade", "head"), ("check",)]:
                    self.command(
                        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *action],
                        env=env | {"SCC_DATABASE_URL": url},
                    )
        combined = ET.Element("testsuites")
        groups = {}
        for name, path in [
            ("shared", "tests/contract/shared"),
            ("auth", "tests/integration/auth"),
            ("registry", "tests/integration/registry"),
            ("collection", "tests/integration/collection"),
        ]:
            # Existing A2/A3 suites have same-named fixtures; isolate their pytest processes.
            output = "postgres-" + name
            result = self.pytest("postgres", env, paths=[path], name=output)
            cleaned = self.work / (output + "-sanitized.xml")
            try:
                groups[name] = junit(self.work / (output + ".xml"), cleaned, [password])
            finally:
                if cleaned.exists():
                    combined.extend(ET.parse(cleaned).getroot().findall("testsuite"))
                    ET.ElementTree(combined).write(
                        self.reports / "postgres.xml", encoding="utf-8", xml_declaration=True
                    )
            if result.returncode:
                raise CheckFailed("PostgreSQL tests failed")
        counts = {
            key: sum(group[key] for group in groups.values())
            for key in ("tests", "errors", "failures", "skipped")
        }
        inspected = self.inspect_pg()
        bindings = inspected["NetworkSettings"]["Ports"]["5432/tcp"]
        if not bindings or any(row["HostIp"] != "127.0.0.1" for row in bindings):
            raise CheckFailed("PostgreSQL test port is not loopback")
        return {"counts": counts, "groups": groups, "server_version": version, "image": image}

    def pytest(self, marker, env=None, *, paths=None, name=None):
        name = name or ("offline" if marker == "not postgres" else "postgres")
        return self.command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "backend/pyproject.toml",
                *(TESTS if paths is None else paths),
                "-m",
                marker,
                "-q",
                "-W",
                "error",
                "-p",
                "no:cacheprovider",
                f"--basetemp={self.work / (name + '-tmp')}",
                f"--junitxml={self.work / (name + '.xml')}",
            ],
            env=env,
            timeout=300,
            check=False,
        )

    def core(self):
        project = self.state["core_project"]
        core_state = stack_tools.prepare(project, free_port())
        self.state["core_owner"] = core_state["owner"]
        write_json(self.work / "owner.json", self.state)
        args = [
            sys.executable,
            "tests/integration/system/check_core.py",
            "--project",
            project,
            "--report",
            str(self.reports / "core.json"),
            "--docker",
            self.state["docker"],
        ]
        if self.state["context"]:
            args += ["--context", self.state["context"]]
        self.command(args, timeout=900)
        value = json.loads((self.reports / "core.json").read_text())
        if value["result"] != "passed" or len(value["checks"]) < 9:
            raise CheckFailed("Container core scenario did not pass")
        return {"checks": len(value["checks"]), "backend_image_id": value["backend_image_id"]}

    def cleanup(self, *, remove_private):
        # Revalidate the marker before any mutation or recursive removal.
        load_state(self.work, self.reports)
        project = self.state["core_project"]
        directory = stack_tools.project_directory(project)
        if directory.exists():
            current = stack_tools.load_state(project)
            if current["owner"] != self.state["core_owner"]:
                raise CheckFailed("Foreign core runtime was preserved")
            stack = stack_tools.Stack(
                project, docker=self.state["docker"], context=self.state["context"]
            )
            stack.down(delete_data=True)
            stack.remove_test_runtime()
        for command in [["ps", "-aq"], ["volume", "ls", "-q"], ["network", "ls", "-q"]]:
            result = self.command(
                [*self.docker, *command, "--filter", f"label=com.docker.compose.project={project}"]
            )
            if result.stdout.strip():
                raise CheckFailed("Core Docker resources remain after cleanup")
        pg = self.inspect_pg()
        if pg is not None:
            self.command([*self.docker, "rm", "--force", "--volumes", pg["Id"]], timeout=45)
        if self.inspect_pg() is not None:
            raise CheckFailed("Owned PostgreSQL container remains after cleanup")
        cleanup = {
            "result": "passed",
            "core_containers_volumes_networks": 0,
            "postgres_containers": 0,
            "private_removed": remove_private,
        }
        if remove_private:
            # Exact resolved work directory and marker were checked above; never use ROOT.
            paths = list(self.work.rglob("*"))
            if any(not path.resolve().is_relative_to(self.work) for path in paths):
                raise CheckFailed("Private work contains a path outside its owned directory")
            for path in paths:
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o600)
            shutil.rmtree(self.work)
        write_json(self.reports / "cleanup.json", cleanup)
        return cleanup


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "cleanup"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--docker")
    parser.add_argument("--context")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == "cleanup":
        if not args.work_dir.exists():
            print("No owned CI state remains")
            return 0
        cleanup = Runner(load_state(args.work_dir, args.report_dir)).cleanup(remove_private=True)
        summary_path = args.report_dir / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["cleanup"] = cleanup
            summary["final_cleanup_at"] = utc()
            write_json(summary_path, summary)  # Preserve the failed run result.
        print("Owned CI resources and private files removed")
        return 0
    if sys.version_info[:2] != (3, 12):
        raise CheckFailed("Application checks require Python 3.12")
    state = prepare(args.work_dir, args.report_dir, args.docker, args.context)
    runner = Runner(state)
    summary = {"stage": "A4.4a", "result": "failed", "started_at": utc(), "checks": []}
    try:
        source = runner.command(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"]
        )
        summary["checkout_sha"] = source.stdout.decode().strip()
        names = (
            runner.command(
                [
                    "git",
                    "-c",
                    f"safe.directory={ROOT.as_posix()}",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ]
            )
            .stdout.decode()
            .split("\x00")
        )
        summary["source_sha256"] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sorted(set(names))
            if name and (ROOT / name).is_file()
        }
        summary["source_digest"] = hashlib.sha256(
            json.dumps(summary["source_sha256"], sort_keys=True).encode()
        ).hexdigest()
        summary["python"] = sys.version.split()[0]
        for name, command in [
            (
                "ruff",
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--config",
                    "backend/pyproject.toml",
                    *LINT,
                ],
            ),
            (
                "format",
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "format",
                    "--check",
                    "--config",
                    "backend/pyproject.toml",
                    *LINT,
                ],
            ),
            ("contract", [sys.executable, "docs/design/a/check_contract.py"]),
        ]:
            summary["phase"] = name
            print("RUN: " + name, flush=True)
            runner.command(command, static_output=True)
            summary["checks"].append({"name": name, "result": "passed"})
        for name in ("offline", "postgres", "core"):
            summary["phase"] = name
            print("RUN: " + name, flush=True)
            if name == "offline":
                result = runner.pytest("not postgres")
                facts = {
                    "counts": junit(runner.work / "offline.xml", runner.reports / "offline.xml")
                }
                if result.returncode:
                    raise CheckFailed("Offline tests failed")
            else:
                facts = getattr(runner, name)()
            summary["checks"].append({"name": name, "result": "passed", **facts})
        summary["result"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        summary["error_type"] = type(error).__name__
        print(
            "Application checks failed; inspect the public summary and private local diagnostics",
            flush=True,
        )
    finally:
        try:
            summary["cleanup"] = runner.cleanup(remove_private=summary["result"] == "passed")
        except (Exception, KeyboardInterrupt) as error:
            summary["result"] = "failed"
            summary["cleanup"] = {"result": "failed", "error_type": type(error).__name__}
        summary["completed_at"] = utc()
        write_json(runner.reports / "summary.json", summary)
    print(
        json.dumps({key: value for key, value in summary.items() if key != "source_sha256"}),
        flush=True,
    )
    return 0 if summary["result"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        print("CI setup or cleanup failed; no private values were printed", file=sys.stderr)
        raise SystemExit(1) from None
