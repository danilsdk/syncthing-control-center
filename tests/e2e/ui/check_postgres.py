"""Run browser -> real API -> private PostgreSQL -> worker, with adapter doubles."""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[3]


def unused_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    initdb, pg_ctl = [args.bin_dir / (name + suffix) for name in ["initdb", "pg_ctl"]]
    if not initdb.is_file() or not pg_ctl.is_file():
        parser.error("PostgreSQL binaries not found")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("SCC_", "PG", "VITE_", "B3_"))
    }
    env.update(
        PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), str(Path(__file__).parent)]),
        PYTHONIOENCODING="utf-8",
    )
    directory = Path(tempfile.mkdtemp(prefix="scc-b3-pg-")).resolve()
    data, api, server_log = directory / "data", None, None

    def run(command, timeout=120, cwd=ROOT):
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [str(p) for p in command],
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            output.seek(0)
            print(output.read().decode("utf-8", errors="replace"), end="", flush=True)
        result.check_returncode()

    try:
        password = secrets.token_urlsafe(32)
        pwfile = directory / "password"
        pwfile.write_text(password, encoding="utf-8")
        pwfile.chmod(0o600)
        port, api_port = unused_port(), unused_port()
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
        with (data / "postgresql.conf").open("a") as config:
            config.write(
                f"\nlisten_addresses='127.0.0.1'\nport={port}\nunix_socket_directories=''\nmax_connections=30\ntimezone='UTC'\n"
            )
        run([pg_ctl, "-D", data, "-l", directory / "postgres.log", "-w", "start"])
        with psycopg.connect(
            host="127.0.0.1",
            port=port,
            dbname="postgres",
            user="scc_test",
            password=password,
            autocommit=True,
        ) as connection:
            connection.execute("CREATE DATABASE scc_b3_browser")
        env["SCC_DATABASE_URL"] = URL.create(
            "postgresql+psycopg",
            username="scc_test",
            password=password,
            host="127.0.0.1",
            port=port,
            database="scc_b3_browser",
        ).render_as_string(hide_password=False)
        env["SCC_SESSION_SECRET"] = secrets.token_urlsafe(48)
        run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"])
        env.update(B3_API_TARGET=f"http://127.0.0.1:{api_port}", B3_REAL_API="1")
        server_log = (directory / "api.log").open("w+")
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "application:create_test_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while True:
            if api.poll() is not None:
                server_log.seek(0)
                print(server_log.read())
                raise RuntimeError("Test API stopped")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{api_port}/health/ready", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Test API did not become ready")
            time.sleep(0.1)
        run([shutil.which("npm") or "npm", "run", "test:e2e"], timeout=180, cwd=ROOT / "frontend")
        if args.report:
            args.report.write_text(
                json.dumps(
                    {
                        "result": "passed",
                        "transport": (
                            "Chromium -> Vite proxy -> real A2/A3/B1/B2 HTTP API -> private "
                            "PostgreSQL -> singleton worker; Syncthing adapter doubles"
                        ),
                        "syncthing_live": False,
                        "source_commit": subprocess.check_output(
                            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                        ).strip(),
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        if api is not None and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=20)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)
        if server_log is not None:
            server_log.close()
        if (data / "postmaster.pid").exists():
            run([pg_ctl, "-D", data, "-w", "-t", "30", "-m", "fast", "stop"])
        if (
            not (data / "postmaster.pid").exists()
            and directory.parent == Path(tempfile.gettempdir()).resolve()
            and directory.name.startswith("scc-b3-pg-")
        ):
            shutil.rmtree(directory)
            print("Owned API/worker/PostgreSQL stopped and removed.", flush=True)


if __name__ == "__main__":
    main()
