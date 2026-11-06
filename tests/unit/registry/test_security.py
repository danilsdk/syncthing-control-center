import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from app.contracts.errors import AppError
from app.registry.security import (
    InvalidSession,
    LoginLimiter,
    PasswordHasher,
    PasswordHashError,
    SessionCodec,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
SECRET = "public-unit-session-secret-" * 2


def test_real_scrypt_salts_roundtrip_and_no_password_normalization():
    hasher = PasswordHasher()
    password = "public canary password Я "
    first, second = hasher.hash(password), hasher.hash(password)
    assert first != second and password not in first
    assert hasher.verify(password, first)
    assert not hasher.verify(password.rstrip(), first)


@pytest.mark.parametrize("bad", ["", "sha256$plaintext", "$scrypt$v1$n=999999999,r=8,p=1$a$b"])
def test_invalid_hash_cannot_select_arbitrary_kdf_parameters(bad, monkeypatch):
    monkeypatch.setattr(hashlib, "scrypt", lambda *_a, **_k: pytest.fail("Unbounded KDF"))
    with pytest.raises(PasswordHashError):
        PasswordHasher().verify("any password", bad)


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_bootstrap_password_bounds(password):
    with pytest.raises(ValueError):
        PasswordHasher().hash(password)


def test_session_expiry_is_absolute_and_signature_is_key_bound():
    user = uuid4()
    codec = SessionCodec(SECRET)
    token, claims = codec.issue(user, NOW)
    assert claims.user_id == user and claims.expires_at == NOW + timedelta(hours=8)
    assert codec.read(token, NOW + timedelta(hours=7)).expires_at == claims.expires_at
    with pytest.raises(InvalidSession):
        codec.read(token, claims.expires_at)
    with pytest.raises(InvalidSession):
        SessionCodec(SECRET + "rotated").read(token, NOW)
    assert claims.csrf_token not in repr(claims)


@pytest.mark.parametrize("token", [None, "", "x", "a.b.c", "x" * 2049, "a=.b", "ю.я"])
def test_malformed_session_is_rejected_safely(token):
    with pytest.raises(InvalidSession):
        SessionCodec(SECRET).read(token, NOW)


@pytest.mark.parametrize("change", ["payload", "mac"])
def test_cookie_tampering(change):
    token, _ = SessionCodec(SECRET).issue(uuid4(), NOW)
    payload, mac = token.split(".")
    if change == "payload":
        payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    else:
        mac = ("A" if mac[0] != "A" else "B") + mac[1:]
    with pytest.raises(InvalidSession):
        SessionCodec(SECRET).read(payload + "." + mac, NOW)


@pytest.mark.parametrize(
    "bad_payload",
    [
        '{"user_id":"x","expires_at":true,"csrf_token":"x"}',
        '{"user_id":"x","user_id":"y","expires_at":1,"csrf_token":"x"}',
        "[1,2,3]",
        json.dumps({"user_id": str(uuid4()), "expires_at": 10**100, "csrf_token": "x"}),
    ],
)
def test_even_correctly_signed_invalid_claims_are_rejected(bad_payload):
    payload = base64.urlsafe_b64encode(bad_payload.encode()).rstrip(b"=")
    mac = hmac.digest(SECRET.encode(), b"scc:admin-session:v1\x00" + payload, "sha256")
    token = payload.decode() + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    with pytest.raises(InvalidSession):
        SessionCodec(SECRET).read(token, NOW)


def test_limiter_exact_window_and_failed_work_releases_slot():
    tick = [0.0]
    limiter = LoginLimiter(monotonic=lambda: tick[0], peer_limit=2)
    with pytest.raises(RuntimeError), limiter.reserve("peer"):
        raise RuntimeError("failed password check")
    with limiter.reserve("peer"):
        pass
    with pytest.raises(AppError) as error, limiter.reserve("peer"):
        pytest.fail("Limit bypassed")
    assert error.value.status_code == 429
    tick[0] = 60
    with limiter.reserve("peer"):
        pass


def test_global_limit_is_shared_by_different_peers():
    limiter = LoginLimiter(total_limit=2)
    for peer in ["one", "two"]:
        with limiter.reserve(peer):
            pass
    with pytest.raises(AppError), limiter.reserve("three"):
        pytest.fail("Global limit bypassed")


def test_peer_memory_is_bounded_and_expires():
    tick = [0.0]
    limiter = LoginLimiter(monotonic=lambda: tick[0], max_peers=1)
    with limiter.reserve("first"):
        pass
    with pytest.raises(AppError), limiter.reserve("second"):
        pytest.fail("Peer bound bypassed")
    tick[0] = 60
    with limiter.reserve("second"):
        pass


def test_only_one_password_check_can_run_concurrently():
    limiter = LoginLimiter()
    entered, release = Event(), Event()

    def hold():
        with limiter.reserve("first"):
            entered.set()
            assert release.wait(3)

    thread = Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(AppError), limiter.reserve("second"):
            pytest.fail("Concurrent KDF admitted")
    finally:
        release.set()
        thread.join(timeout=3)
    with limiter.reserve("second"):
        pass
