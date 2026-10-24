"""Run A0.3 tests in a private PostgreSQL cluster, never an existing service/database."""

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


def run(command, *, env=None):
    # A background postgres can inherit pg_ctl's handles on Windows. A file avoids
    # waiting for EOF on a pipe that stays open for the whole server lifetime.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=HIDDEN,
            timeout=180,
        )
        output.seek(0)
        print(output.read().decode("utf-8", errors="replace"), end="", flush=True)
    result.check_returncode()
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, help="Directory containing initdb and pg_ctl")
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    initdb = args.bin_dir / f"initdb{suffix}" if args.bin_dir else shutil.which("initdb")
    pg_ctl = args.bin_dir / f"pg_ctl{suffix}" if args.bin_dir else shutil.which("pg_ctl")
    if not initdb or not pg_ctl or not Path(initdb).is_file() or not Path(pg_ctl).is_file():
        parser.error("PostgreSQL binaries not found; use --bin-dir")

    # Filter application settings and libpq settings from the user's environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SCC_", "PG"))}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="scc-a03-pg-", delete=False) as temporary:
        directory = Path(temporary).resolve()
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
                env=env,
            )
            pwfile.unlink()
            with (data / "postgresql.conf").open("a", encoding="utf-8") as config:
                config.write(
                    f"\nlisten_addresses = '127.0.0.1'\nport = {port}\n"
                    "unix_socket_directories = ''\nmax_connections = 20\ntimezone = 'UTC'\n"
                )
            # Only pg_ctl pointed at this freshly created data directory is ever stopped.
            try:
                run(
                    [
                        pg_ctl,
                        "-D",
                        data,
                        "-l",
                        directory / "postgres.log",
                        "-w",
                        "-t",
                        "30",
                        "start",
                    ],
                    env=env,
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
                    print("Private PostgreSQL:", connection.info.server_version, flush=True)
                    connection.execute("CREATE DATABASE scc_a03_tests")
                url = URL.create(
                    "postgresql+psycopg",
                    username="scc_test",
                    password=password,
                    host="127.0.0.1",
                    port=port,
                    database="scc_a03_tests",
                ).render_as_string(hide_password=False)
                env["SCC_TEST_DATABASE_URL"] = url
                env["SCC_TEST_PRIVATE_CLUSTER"] = "a03-private-cluster"
                # Exercise the documented CLI and settings path against the empty database too.
                migration_env = env | {"SCC_DATABASE_URL": url}
                for action in [("upgrade", "head"), ("check",)]:
                    run(
                        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *action],
                        env=migration_env,
                    )
                run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-c",
                        "backend/pyproject.toml",
                        "tests/contract/shared",
                        "-m",
                        "postgres",
                        "-q",
                        "-o",
                        f"cache_dir={directory / 'pytest-cache'}",
                    ],
                    env=env,
                )
            except subprocess.CalledProcessError as error:
                log = directory / "postgres.log"
                if Path(error.cmd[0]).name == Path(pg_ctl).name and log.is_file():
                    print(log.read_text(encoding="utf-8", errors="replace")[-8000:], flush=True)
                raise
            finally:
                if (data / "postmaster.pid").exists():
                    run([pg_ctl, "-D", data, "-w", "-t", "30", "-m", "fast", "stop"], env=env)
        finally:
            if (data / "postmaster.pid").exists():
                print(
                    f"Private cluster still running; retained its data at {directory}", flush=True
                )
            else:
                # Verify the exact owned target immediately before recursive cleanup.
                if directory.parent != Path(
                    tempfile.gettempdir()
                ).resolve() or not directory.name.startswith("scc-a03-pg-"):
                    raise RuntimeError("Refusing cleanup outside the owned temporary directory")
                shutil.rmtree(directory)
    print("Private cluster stopped and removed; existing services were not used.", flush=True)


if __name__ == "__main__":
    main()
