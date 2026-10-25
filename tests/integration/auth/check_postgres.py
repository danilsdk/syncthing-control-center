"""Run A2 auth tests against a fresh owned PostgreSQL cluster, never a working DB."""

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[3]
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(command, env, *, timeout=150):
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [str(p) for p in command],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=HIDDEN,
            timeout=timeout,
        )
        output.seek(0)
        print(output.read().decode("utf-8", errors="replace"), end="", flush=True)
    result.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument(
        "--suite", choices=("auth", "registry", "all", "registry-live"), default="auth"
    )
    parser.add_argument("--live-report", type=Path)
    args = parser.parse_args()
    if args.live_report and args.suite != "registry-live":
        parser.error("--live-report requires --suite registry-live")
    suffix = ".exe" if os.name == "nt" else ""
    initdb, pg_ctl = (args.bin_dir / (name + suffix) for name in ["initdb", "pg_ctl"])
    if not initdb.is_file() or not pg_ctl.is_file():
        parser.error("PostgreSQL binaries not found")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("SCC_", "PG"))}
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONIOENCODING="utf-8")
    directory = Path(tempfile.mkdtemp(prefix="scc-a22-pg-")).resolve()
    data = directory / "data"
    try:
        password = secrets.token_urlsafe(32)
        pwfile = directory / "password"
        pwfile.write_text(password, encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        run(
            [
                initdb,
                "-D",
                data,
                "--username=scc_test",
                "--auth=scram-sha-256",
                f"--pwfile={pwfile}",
                "--encoding=UTF8",
                "--no-locale",
            ],
            env,
        )
        pwfile.unlink()
        with (data / "postgresql.conf").open("a", encoding="utf-8") as output:
            output.write(
                f"\nlisten_addresses = '127.0.0.1'\nport = {port}\n"
                "unix_socket_directories = ''\nmax_connections = 20\ntimezone = 'UTC'\n"
            )
        try:
            run(
                [pg_ctl, "-D", data, "-l", directory / "postgres.log", "-w", "-t", "30", "start"],
                env,
            )
            with psycopg.connect(
                host="127.0.0.1",
                port=port,
                dbname="postgres",
                user="scc_test",
                password=password,
                autocommit=True,
                connect_timeout=3,
            ) as connection:
                connection.execute("CREATE DATABASE scc_a22_tests")
            url = URL.create(
                "postgresql+psycopg",
                username="scc_test",
                password=password,
                host="127.0.0.1",
                port=port,
                database="scc_a22_tests",
            ).render_as_string(hide_password=False)
            env.update(
                SCC_AUTH_TEST_DATABASE_URL=url,
                SCC_AUTH_TEST_PRIVATE_CLUSTER="a22-private-cluster",
                SCC_DATABASE_URL=url,
            )
            for action in [("upgrade", "head"), ("check",)]:
                run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *action], env)
            if args.suite == "registry-live":
                command = [sys.executable, "tests/integration/registry/check_live.py"]
                if args.live_report:
                    command.extend(["--report", str(args.live_report.resolve())])
                run(command, env, timeout=420)
                return
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "backend/pyproject.toml",
            ]
            if args.suite in {"auth", "all"}:
                command.append("tests/integration/auth")
            if args.suite in {"registry", "all"}:
                command.append("tests/integration/registry")
            command += [
                "-m",
                "postgres",
                "-q",
                "-o",
                f"cache_dir={directory / 'pytest-cache'}",
            ]
            if args.junitxml:
                command.append(f"--junitxml={args.junitxml}")
            run(command, env)
        finally:
            if (data / "postmaster.pid").exists():
                run([pg_ctl, "-D", data, "-w", "-t", "30", "-m", "fast", "stop"], env)
    finally:
        if (data / "postmaster.pid").exists():
            print(f"Owned cluster remains running; retained {directory}", flush=True)
        else:
            if directory.parent != Path(
                tempfile.gettempdir()
            ).resolve() or not directory.name.startswith("scc-a22-pg-"):
                raise RuntimeError("Refusing cleanup outside the owned cluster")
            shutil.rmtree(directory)
    print(
        "Private auth PostgreSQL stopped and removed; working services were not used.", flush=True
    )


if __name__ == "__main__":
    main()
