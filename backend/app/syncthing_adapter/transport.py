"""Small synchronous HTTP boundary: no redirects, proxy environment or retries."""

import http.client
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock, Timer
from typing import Protocol
from urllib.parse import urlsplit

from app.contracts.interfaces import AdapterError, NodeConnection

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Response:
    body: bytes = field(repr=False)
    content_type: str
    device_id: str | None = None


class Transport(Protocol):
    def request(
        self, method: str, path: str, *, body: bytes | None = None, timeout: float = 5
    ) -> Response: ...
    def close(self) -> None: ...


class HttpTransport:
    def __init__(self, connection: NodeConnection, *, tls_context: ssl.SSLContext | None = None):
        self._origin = urlsplit(connection.base_url.get_secret_value())
        self._key = connection.api_key.get_secret_value()
        if not self._key.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in self._key):
            raise ValueError("Invalid Syncthing API key configuration")
        self._tls = tls_context or ssl.create_default_context()
        if not self._tls.check_hostname or self._tls.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("Syncthing HTTPS requires certificate and hostname verification")
        self._closed = False
        self._lock = RLock()
        self._active: dict[http.client.HTTPConnection, Callable[[], None]] = {}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for abort in tuple(self._active.values()):
                abort()

    def request(
        self, method: str, path: str, *, body: bytes | None = None, timeout: float = 5
    ) -> Response:
        write = method == "PATCH"
        operation = "write" if write else "read"
        if method not in {"GET", "PATCH"} or not path.startswith("/rest/"):
            raise ValueError("Unsupported Syncthing transport operation")
        if not 0 < timeout <= 20:
            raise ValueError("Invalid Syncthing HTTP timeout")
        if self._origin.scheme == "https":
            connection = http.client.HTTPSConnection(
                self._origin.hostname, self._origin.port, timeout=timeout, context=self._tls
            )
        else:
            connection = http.client.HTTPConnection(
                self._origin.hostname, self._origin.port, timeout=timeout
            )
        connection.auto_open = 0  # An owner closing the socket must not trigger a new connection.
        wire_socket = None

        def abort():
            # HTTPConnection may detach a Connection: close socket into HTTPResponse.
            if wire_socket is not None:
                try:
                    wire_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

        with self._lock:
            if self._closed:
                raise AdapterError(
                    "unreachable",
                    operation=operation,
                    write_outcome="not_applied" if write else None,
                )
            self._active[connection] = abort
        deadline = time.monotonic() + timeout
        timer = Timer(timeout, abort)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            # A close during connect cannot abort a socket that does not exist yet.
            # Publish it and linearize the start of sending with the owner's close.
            # The watchdog can still abort a blocked send; response waits hold no lock.
            with self._lock:
                wire_socket = connection.sock
                if self._closed:
                    raise AdapterError(
                        "unreachable",
                        operation=operation,
                        write_outcome="not_applied" if write else None,
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if wire_socket is None:
                    raise OSError
                wire_socket.settimeout(max(0.001, deadline - time.monotonic()))
                connection.request(
                    method,
                    path,
                    body=body,
                    headers={
                        "X-API-Key": self._key,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                        **({"Content-Type": "application/json"} if write else {}),
                    },
                )
            with connection.getresponse() as response:
                if response.status in {401, 403, 404}:
                    raise AdapterError(
                        "not_found" if response.status == 404 else "auth_error",
                        operation=operation,
                        write_outcome="not_applied" if write else None,
                    )
                if response.status not in ({200, 204} if write else {200}):
                    raise AdapterError("invalid_response", operation=operation)
                if write:
                    # An acknowledgement is not evidence that paused has taken effect.
                    return Response(b"", "", response.getheader("X-Syncthing-ID"))
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise AdapterError("invalid_response")
                chunks, size = [], 0
                while not response.isclosed():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    wire_socket.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise AdapterError("invalid_response")
                    chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if response.length not in {None, 0}:
                    raise AdapterError("invalid_response")
                return Response(
                    b"".join(chunks),
                    response.getheader("Content-Type", ""),
                    response.getheader("X-Syncthing-ID"),
                )
        except TimeoutError:
            raise AdapterError("timeout", operation=operation) from None
        except (http.client.IncompleteRead, http.client.BadStatusLine) as error:
            code = (
                "unreachable"
                if isinstance(error, http.client.RemoteDisconnected)
                else "invalid_response"
            )
            if time.monotonic() >= deadline:
                code = "timeout"
            raise AdapterError(code, operation=operation) from None
        except (OSError, http.client.HTTPException, UnicodeError, ValueError):
            code = "timeout" if time.monotonic() >= deadline else "unreachable"
            raise AdapterError(code, operation=operation) from None
        finally:
            timer.cancel()
            try:
                abort()
            finally:
                with self._lock:
                    self._active.pop(connection, None)
