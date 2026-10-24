"""Run the installed application in a temporary loopback-only Uvicorn process."""

import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from uuid import UUID


def main():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("SCC_")}
    environment["SCC_ENVIRONMENT"] = "test"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    opener = build_opener(ProxyHandler({}))
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise RuntimeError("Temporary server exited before becoming live")
            try:
                with opener.open(url + "/health/live", timeout=0.5) as response:
                    assert response.status == 200
                    assert json.load(response) == {"status": "ok"}
                    UUID(response.headers["X-Request-ID"])
                break
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Temporary server did not become live in 10 seconds") from None
                time.sleep(0.05)
        try:
            opener.open(url + "/health/ready", timeout=2)
        except HTTPError as response:
            assert response.code == 503
            payload = json.load(response)
            assert payload["error"]["code"] == "service_unavailable"
            assert payload["error"]["request_id"] == response.headers["X-Request-ID"]
        else:
            raise AssertionError("Unconfigured database must not report readiness")
        print("Real HTTP: /health/live=200, /health/ready=503, request IDs verified")
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        print("Temporary Uvicorn process stopped")


if __name__ == "__main__":
    main()
