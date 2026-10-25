"""Run A3 SQL tests in an owned temporary cluster; no working database URL is accepted."""

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


def run(command, env):
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [str(p) for p in command],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=HIDDEN,
            timeout=150,
        )
        output.seek(0)
        print(output.read().decode("utf-8", errors="replace"), end="", flush=True)
    result.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    initdb, pg_ctl = (args.bin_dir / (name + suffix) for name in ("initdb", "pg_ctl"))
    if not initdb.is_file() or not pg_ctl.is_file():
        parser.error("PostgreSQL binaries not found")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SCC_", "PG"))
    }
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONIOENCODING="utf-8")
    directory = Path(tempfile.mkdtemp(prefix="scc-a32-pg-")).resolve()
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
        with (data / "postgresql.conf").open("a", encoding="utf-8") as handle:
            handle.write(
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
                connection.execute("CREATE DATABASE scc_a32_tests")
            url = URL.create(
                "postgresql+psycopg",
                username="scc_test",
                password=password,
                host="127.0.0.1",
                port=port,
                database="scc_a32_tests",
            ).render_as_string(hide_password=False)
            env.update(
                SCC_DATABASE_URL=url,
                SCC_COLLECTION_TEST_DATABASE_URL=url,
                SCC_COLLECTION_TEST_PRIVATE_CLUSTER="a32-private-cluster",
            )
            for action in [("upgrade", "head"), ("check",)]:
                run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *action], env)
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "backend/pyproject.toml",
                "tests/integration/collection",
                "-m",
                "postgres",
                "-q",
                "-W",
                "error",
                "-o",
                f"cache_dir={directory / 'pytest-cache'}",
            ]
            if args.junitxml:
                command.append(f"--junitxml={args.junitxml.resolve()}")
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
            ).resolve() or not directory.name.startswith("scc-a32-pg-"):
                raise RuntimeError("Refusing cleanup outside the owned cluster")
            shutil.rmtree(directory)
    print(
        "Private collection PostgreSQL stopped and removed; working services were not used.",
        flush=True,
    )


if __name__ == "__main__":
    main()
