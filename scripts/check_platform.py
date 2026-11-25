"""Check runner architecture and run the accepted native integration on macOS."""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy/syncthing/runtime"
BENCH = "deploy/syncthing/bench.py"
AUTH_SQL = "tests/integration/auth/check_postgres.py"
COLLECTION_SQL = "tests/integration/collection/check_postgres.py"
ARCHITECTURES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def report(message):
    print(message, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(f"- {message}\n")


def require_runtime(system, architecture):
    actual_system = platform.system()
    machine = platform.machine()
    os_version = platform.release()
    if actual_system == "Darwin":
        os_version = platform.mac_ver()[0]
    elif actual_system == "Linux":
        os_version = platform.freedesktop_os_release().get("PRETTY_NAME", os_version)
    report(
        f"OS {actual_system} {os_version} ({platform.version()}), "
        f"Python {platform.python_version()} {machine}"
    )
    if (actual_system, ARCHITECTURES.get(machine.lower())) != (system, architecture):
        raise RuntimeError(f"Expected {system} / {architecture}")
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError("Expected 64-bit Python 3.12")


def version(*command):
    return subprocess.check_output(command, text=True, timeout=20).strip()


def inspect_javascript(architecture):
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        raise RuntimeError("Node and npm are required")
    actual = version(node, "-p", "process.arch")
    report(f"Node {version(node, '--version')} {actual}, npm {version(npm, '--version')}")
    if actual != architecture:
        raise RuntimeError(f"Expected Node architecture {architecture}")


def run_step(command, log, env, *, timeout=720):
    """Allow the owned runner and its children to unwind before forcing an exit."""
    with (
        log.open("w", encoding="utf-8") as output,
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process,
    ):
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if process.poll() is None:
                interrupt(process)
            raise


def interrupt(process):
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def check_sql_report(path):
    suites = list(ET.parse(path).iter("testsuite"))
    tests = sum(int(suite.attrib["tests"]) for suite in suites)
    if not tests or any(
        int(suite.attrib[name]) for suite in suites for name in ("failures", "errors", "skipped")
    ):
        raise RuntimeError(f"{path.stem} did not run a complete SQL suite")
    report(f"{path.stem}: {tests} SQL tests passed (0 skipped)")


def check_live_report(path, *, cleanup):
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("result") != "passed" or any(
        evidence.get("cleanup", {}).get(key) != value for key, value in cleanup.items()
    ):
        raise RuntimeError(f"{path.stem} did not confirm success and cleanup")
    return evidence


def native_checks(postgres):
    require_runtime("Darwin", "arm64")
    report(version(postgres / "postgres", "--version"))
    if RUNTIME.resolve() != RUNTIME:
        raise RuntimeError("Native runtime cannot traverse a symbolic link")
    RUNTIME.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="b6-", dir=RUNTIME))
    pg_tmp = directory / "postgres"
    pg_tmp.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SCC_", "PG", "PYTEST_"))
    }
    env.update(TMPDIR=str(pg_tmp), PYTHONPATH=str(ROOT / "backend"), PYTHONIOENCODING="utf-8")

    def run(label, script, *args, **extra_env):
        log = directory / f"{label}.log"
        report(f"{label}: running")
        try:
            code = run_step([sys.executable, script, *args], log, env | extra_env)
        except subprocess.TimeoutExpired:
            report(f"{label}: timed out")
            raise
        except KeyboardInterrupt:
            report(f"{label}: interrupted")
            raise
        report(f"{label}: exit {code}")
        if code != 0:
            raise RuntimeError(f"{label} failed; see private {label}.log")
        return log

    baseline = run("bench-before", BENCH, "status")
    if json.loads(baseline.read_text(encoding="utf-8")).get("running") != []:
        raise RuntimeError("Existing native bench is running in this worktree")

    sql_args = ("--bin-dir", postgres)
    try:
        run("bench-transfer", BENCH, "verify")
        # A clean bench can print download progress before its JSON output.
        bench = check_live_report(RUNTIME / "verification.json", cleanup={"running": []})
        if bench.get("nodes_ready") != 2:
            raise RuntimeError("Expected both native Syncthing nodes")
        report(f"Syncthing {bench['version']}: two nodes verified via REST")

        run("adapter-d01", "tests/integration/syncthing_adapter/check_d01.py")
        check_live_report(RUNTIME / "d01-verification.json", cleanup={"running": []})

        schema = directory / "schema-sql.xml"
        # The existing schema runner invokes pytest once and accepts options via its environment.
        run(
            "schema-sql",
            "docs/design/a/check_postgres.py",
            *sql_args,
            PYTEST_ADDOPTS=f'--junitxml="{schema}"',
        )
        check_sql_report(schema)
        auth = directory / "auth-registry-sql.xml"
        run("auth-registry-sql", AUTH_SQL, *sql_args, "--suite", "all", "--junitxml", auth)
        check_sql_report(auth)
        collection = directory / "collection-sql.xml"
        run("collection-sql", COLLECTION_SQL, *sql_args, "--junitxml", collection)
        check_sql_report(collection)

        cleanup = {"api_running": False, "syncthing_running": []}
        registry_live = directory / "registry-live.json"
        run(
            "registry-live",
            AUTH_SQL,
            *sql_args,
            "--suite",
            "registry-live",
            "--live-report",
            registry_live,
        )
        registry = check_live_report(registry_live, cleanup=cleanup)
        report(f"Registry API: {len(registry['checks'])} live checks")

        collection_live = directory / "collection-live.json"
        run("collection-live", COLLECTION_SQL, *sql_args, "--live-report", collection_live)
        collector = check_live_report(
            collection_live, cleanup=cleanup | {"fixture_directories_present": False}
        )
        report(f"Collector and history API: {len(collector['checks'])} live checks")
    finally:
        final = run("bench-after", BENCH, "status")
        running = json.loads(final.read_text(encoding="utf-8")).get("running")
        leftover_clusters = len(list(pg_tmp.iterdir()))
        report(f"Cleanup: Syncthing running={running}; PostgreSQL temp entries={leftover_clusters}")
        if running != [] or leftover_clusters:
            raise RuntimeError("Native cleanup incomplete; private runtime retained")
        pg_tmp.rmdir()


def terminate(_signum, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    environment = subcommands.add_parser("environment")
    environment.add_argument("--os", choices=("Linux", "Windows", "Darwin"), required=True)
    environment.add_argument("--arch", choices=("x64", "arm64"), required=True)
    native = subcommands.add_parser("native")
    native.add_argument("--pg-bin", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "environment":
            require_runtime(args.os, args.arch)
            inspect_javascript(args.arch)
        else:
            signal.signal(signal.SIGTERM, terminate)
            native_checks(args.pg_bin.resolve())
    except KeyboardInterrupt:
        report("Platform check interrupted")
        return 130
    except (
        OSError,
        ValueError,
        KeyError,
        ET.ParseError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        report(f"Platform check failed ({type(error).__name__}): {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
