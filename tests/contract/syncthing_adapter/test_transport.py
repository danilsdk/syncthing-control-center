"""Real local HTTP sockets, without a live Syncthing or personal credentials."""

import http.client
import json
import socket
import ssl
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID

import pytest
from app.contracts.interfaces import AdapterError, NodeConnection
from app.syncthing_adapter import HttpSyncthingAdapter
from app.syncthing_adapter.transport import HttpTransport

KEY = "transport-unit-secret"
DEVICE = "DEMO-EXPECTED-DEVICE"
SYSTEM = json.dumps({"myID": DEVICE, "startTime": "2026-10-25T00:00:00Z", "uptime": 1}).encode()


@contextmanager
def server(action):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("X-API-Key"), None))
            action(self)

        def do_PATCH(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, self.headers.get("X-API-Key"), body))
            action(self)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as instance:
        thread = Thread(target=lambda: instance.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        try:
            yield instance.server_port, calls
        finally:
            instance.shutdown()
            thread.join(timeout=3)


def respond(handler, status=200, body=None, content_type="application/json", **headers):
    if body is None:
        body = (
            json.dumps(
                [
                    {
                        "id": 1,
                        "globalID": 1,
                        "type": "Starting",
                        "time": "2026-10-25T00:00:00.123456789Z",
                        "data": {},
                    }
                ]
            ).encode()
            if handler.path.startswith("/rest/events")
            else SYSTEM
        )
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Content-Type", content_type)
    handler.send_header("X-Syncthing-ID", DEVICE)
    for key, value in headers.items():
        handler.send_header(key, value)
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass


def connection(port):
    return NodeConnection(node_id=UUID(int=1), base_url=f"http://127.0.0.1:{port}", api_key=KEY)


@pytest.mark.parametrize(
    "status,code,outcome",
    [
        (401, "auth_error", "not_applied"),
        (403, "auth_error", "not_applied"),
        (404, "not_found", "not_applied"),
        (500, "invalid_response", "unknown"),
        (302, "invalid_response", "unknown"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_status_errors_ignore_raw_bodies_and_preserve_write_uncertainty(
    status, code, outcome, method
):
    with server(
        lambda h: respond(h, status, b"upstream secret and private path", "text/plain")
    ) as (port, calls):
        transport = HttpTransport(connection(port))
        with pytest.raises(AdapterError) as caught:
            transport.request(
                method,
                "/rest/config/folders/demo",
                body=b'{"paused":true}' if method == "PATCH" else None,
            )
        assert caught.value.code == code
        assert caught.value.write_outcome == (outcome if method == "PATCH" else None)
        assert "secret" not in repr(caught.value) and "127.0.0.1" not in str(caught.value)
        assert len(calls) == 1
        transport.close()


def test_redirect_never_forwards_credentials_to_another_server():
    with server(lambda h: respond(h)) as (sink_port, sink_calls):
        with server(
            lambda h: respond(
                h, 307, b"", Location=f"http://127.0.0.1:{sink_port}/rest/system/status"
            )
        ) as (port, calls):
            transport = HttpTransport(connection(port))
            with pytest.raises(AdapterError, match="invalid_response"):
                transport.request("GET", "/rest/system/status")
            transport.close()
        assert len(calls) == 1 and sink_calls == []


def test_proxy_environment_is_not_used(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with server(lambda h: respond(h)) as (port, calls):
        client = HttpSyncthingAdapter(connection(port), expected_device_id=DEVICE)
        assert client.read_system().device_id == DEVICE
        assert calls[0][2] == KEY
        client.close()


def test_connection_close_response_can_complete_without_reusing_a_closed_socket():
    with server(lambda h: respond(h, Connection="close")) as (port, _calls):
        client = HttpSyncthingAdapter(connection(port), expected_device_id=DEVICE)
        assert client.read_system().device_id == DEVICE
        client.close()


@pytest.mark.parametrize("content", [b"not JSON", b'{"myID":"one","myID":"two"}'])
def test_real_socket_invalid_json_is_a_safe_adapter_error(content):
    with server(lambda h: respond(h, body=content)) as (port, _calls):
        client = HttpSyncthingAdapter(connection(port))
        with pytest.raises(AdapterError, match="invalid_response"):
            client.read_system()
        client.close()


def test_response_size_is_bounded_before_json_parsing(monkeypatch):
    monkeypatch.setattr("app.syncthing_adapter.transport.MAX_RESPONSE_BYTES", 8)
    with server(lambda h: respond(h, body=b"123456789")) as (port, _calls):
        transport = HttpTransport(connection(port))
        with pytest.raises(AdapterError, match="invalid_response"):
            transport.request("GET", "/rest/system/status")
        transport.close()


def test_truncated_content_length_cannot_be_accepted():
    def truncated(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "200")
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b"{}")
        handler.close_connection = True

    with server(truncated) as (port, _calls):
        transport = HttpTransport(connection(port))
        with pytest.raises(AdapterError, match="invalid_response"):
            transport.request("GET", "/rest/system/status")
        transport.close()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_deadline_covers_slow_headers_and_a_slow_body(phase):
    def slow(handler):
        if phase == "headers":
            time.sleep(0.4)
            respond(handler)
        else:
            handler.send_response(200)
            handler.send_header("Content-Length", "200")
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            for _ in range(20):
                try:
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                except OSError:
                    break
                time.sleep(0.04)

    with server(slow) as (port, _calls):
        transport = HttpTransport(connection(port))
        started = time.monotonic()
        with pytest.raises(AdapterError, match="timeout"):
            transport.request("GET", "/rest/events?since=0&timeout=0", timeout=0.12)
        assert time.monotonic() - started < 1.5
        transport.close()


def test_disconnected_patch_has_unknown_outcome_and_is_never_retried():
    def disconnect_after_accepting(handler):
        if handler.command == "GET":
            respond(handler)
        else:
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)

    with server(disconnect_after_accepting) as (port, calls):
        client = HttpSyncthingAdapter(connection(port), expected_device_id=DEVICE)
        with pytest.raises(AdapterError) as caught:
            client.set_paused("demo", True)
        assert caught.value.code == "unreachable" and caught.value.write_outcome == "unknown"
        assert [call[0] for call in calls] == ["GET", "GET", "GET", "PATCH"]
        assert json.loads(calls[-1][3]) == {"paused": True}
        client.close()


def test_identity_mismatch_after_patch_has_unknown_outcome():
    def action(handler):
        if handler.command == "GET":
            respond(handler)
        else:
            handler.send_response(200)
            handler.send_header("Content-Length", "0")
            handler.send_header("X-Syncthing-ID", "OTHER-DEVICE")
            handler.end_headers()

    with server(action) as (port, calls):
        client = HttpSyncthingAdapter(connection(port), expected_device_id=DEVICE)
        with pytest.raises(AdapterError) as caught:
            client.set_paused("demo", True)
        assert caught.value.code == "identity_mismatch"
        assert caught.value.write_outcome == "unknown"
        assert [call[0] for call in calls] == ["GET", "GET", "GET", "PATCH"]
        client.close()


def test_unreachable_endpoint_and_closed_transport_are_safe():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        # A bound non-listening port cannot accept this connection.
        transport = HttpTransport(connection(port))
        with pytest.raises(AdapterError, match="unreachable|timeout"):
            transport.request("GET", "/rest/system/status", timeout=0.2)
        transport.close()
        with pytest.raises(AdapterError) as caught:
            transport.request("PATCH", "/rest/config/folders/demo", body=b'{"paused":false}')
        assert caught.value.write_outcome == "not_applied"


def test_api_key_cannot_inject_headers_and_insecure_tls_is_rejected():
    bad = NodeConnection(
        node_id=UUID(int=1), base_url="http://127.0.0.1", api_key="key\r\nInjected: value"
    )
    with pytest.raises(ValueError, match="configuration") as caught:
        HttpTransport(bad)
    assert "Injected" not in str(caught.value)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="certificate and hostname"):
        HttpTransport(connection(18384), tls_context=tls)


def test_owner_close_immediately_before_send_cannot_implicitly_reconnect(monkeypatch):
    with server(lambda h: respond(h)) as (port, calls):
        transport = HttpTransport(connection(port))
        original = http.client.HTTPConnection.request
        attempts = []
        connect = http.client.HTTPConnection.connect

        def count_connect(client):
            attempts.append(True)
            return connect(client)

        def close_before_send(client, *args, **kwargs):
            transport.close()
            return original(client, *args, **kwargs)

        monkeypatch.setattr(http.client.HTTPConnection, "connect", count_connect)
        monkeypatch.setattr(http.client.HTTPConnection, "request", close_before_send)
        with pytest.raises(AdapterError) as caught:
            transport.request("PATCH", "/rest/config/folders/demo", body=b'{"paused":true}')
        assert caught.value.write_outcome == "unknown"
        assert len(attempts) == 1 and calls == []
