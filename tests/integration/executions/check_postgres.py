"""Run B2 recovery/API tests in a freshly owned, temporary PostgreSQL cluster."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    initdb = args.bin_dir / f"initdb{suffix}"
    pg_ctl = args.bin_dir / f"pg_ctl{suffix}"
    if not initdb.is_file() or not pg_ctl.is_file():
        parser.error("PostgreSQL binaries not found")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("SCC_", "PG"))}
    env.update(PYTHONPATH=str(ROOT / "backend"), PYTHONIOENCODING="utf-8")
    directory = Path(tempfile.mkdtemp(prefix="scc-b2-pg-")).resolve()
    data = directory / "data"

    def run(command, *, timeout=60):
        # PostgreSQL must not inherit the parent's output pipe on Windows.
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                [str(part) for part in command],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            output.seek(0)
            print(output.read().decode("utf-8", errors="replace"), end="", flush=True)
        completed.check_returncode()

    try:
        password = secrets.token_urlsafe(32)
        pwfile = directory / "password"
        pwfile.write_text(password, encoding="utf-8")
        pwfile.chmod(0o600)
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
            ]
        )
        pwfile.unlink()
        with (data / "postgresql.conf").open("a", encoding="utf-8") as configuration:
            configuration.write(
                f"\nlisten_addresses='127.0.0.1'\nport={port}\n"
                "unix_socket_directories=''\nmax_connections=20\ntimezone='UTC'\n"
            )
        run([pg_ctl, "-D", data, "-l", directory / "postgres.log", "-w", "start"])
        with psycopg.connect(
            host="127.0.0.1",
            port=port,
            dbname="postgres",
            user="scc_test",
            password=password,
            autocommit=True,
            connect_timeout=3,
        ) as connection:
            print("Private PostgreSQL:", connection.info.server_version, flush=True)
            connection.execute("CREATE DATABASE scc_a03_tests")
        env["SCC_TEST_DATABASE_URL"] = URL.create(
            "postgresql+psycopg",
            username="scc_test",
            password=password,
            host="127.0.0.1",
            port=port,
            database="scc_a03_tests",
        ).render_as_string(hide_password=False)
        env["SCC_TEST_PRIVATE_CLUSTER"] = "a03-private-cluster"
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "backend/pyproject.toml",
                "tests/unit/reconciliation",
                "tests/integration/executions",
                "-q",
                "-o",
                f"cache_dir={directory / 'pytest-cache'}",
            ],
            timeout=180,
        )
    finally:
        if (data / "postmaster.pid").exists():
            run([pg_ctl, "-D", data, "-w", "-t", "30", "-m", "fast", "stop"])
        if (data / "postmaster.pid").exists():
            raise RuntimeError("Private cluster still running; its directory was retained")
        if directory.parent != Path(
            tempfile.gettempdir()
        ).resolve() or not directory.name.startswith("scc-b2-pg-"):
            raise RuntimeError("Refusing cleanup outside the owned temporary directory")
        shutil.rmtree(directory)
    print("Private cluster stopped and removed; existing services were not used.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
