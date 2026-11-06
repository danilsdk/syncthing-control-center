"""Bounded wire formats using stdlib scrypt and domain-separated HMAC-SHA256."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

from app.contracts.errors import AppError

SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = "scc_session"
_HASH_PROFILE = "$scrypt$v1$n=131072,r=8,p=1$"
_SIGNING_DOMAIN = b"scc:admin-session:v1\x00"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str, size: int | None = None) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid encoding")
    result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(result) != value or (size is not None and len(result) != size):
        raise ValueError("Invalid encoding")
    return result


class PasswordHashError(ValueError):
    """A configured hash/profile is invalid; never include its value."""


class PasswordHasher:
    @staticmethod
    def _parts(encoded: str) -> tuple[bytes, bytes]:
        try:
            if not encoded.startswith(_HASH_PROFILE) or len(encoded) > 150:
                raise ValueError
            salt, digest = encoded[len(_HASH_PROFILE) :].split("$")
            return _decode(salt, 16), _decode(digest, 32)
        except (ValueError, AttributeError):
            raise PasswordHashError("Administrator password hash is invalid") from None

    def validate(self, encoded: str) -> None:
        self._parts(encoded)

    @staticmethod
    def dummy_hash() -> str:
        # A valid-format random target runs the same KDF without startup hashing.
        return (
            _HASH_PROFILE
            + _encode(secrets.token_bytes(16))
            + "$"
            + _encode(secrets.token_bytes(32))
        )

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**17,
            r=8,
            p=1,
            maxmem=256 * 1024 * 1024,
            dklen=32,
        )

    def hash(self, password: str) -> str:
        if not 15 <= len(password) <= 1024:
            raise ValueError("Administrator password must contain 15 to 1024 characters")
        salt = secrets.token_bytes(16)
        return _HASH_PROFILE + _encode(salt) + "$" + _encode(self._derive(password, salt))

    def verify(self, password: str, encoded: str) -> bool:
        salt, expected = self._parts(encoded)
        if not 1 <= len(password) <= 1024:
            return False
        return hmac.compare_digest(self._derive(password, salt), expected)


@dataclass(frozen=True)
class SessionClaims:
    user_id: UUID
    expires_at: datetime
    csrf_token: str = field(repr=False)


class InvalidSession(ValueError):
    pass


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._key = secret.encode("utf-8")

    def issue(self, user_id: UUID, now: datetime) -> tuple[str, SessionClaims]:
        expiry = int(now.timestamp()) + SESSION_SECONDS
        csrf = _encode(secrets.token_bytes(32))
        payload = _encode(
            json.dumps(
                {"user_id": str(user_id), "expires_at": expiry, "csrf_token": csrf},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        signature = _encode(
            hmac.digest(self._key, _SIGNING_DOMAIN + payload.encode("ascii"), "sha256")
        )
        return payload + "." + signature, SessionClaims(
            user_id, datetime.fromtimestamp(expiry, UTC), csrf
        )

    @staticmethod
    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def read(self, token: str, now: datetime) -> SessionClaims:
        try:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError
            payload, signature = token.split(".")
            supplied = _decode(signature, 32)
            expected = hmac.digest(self._key, _SIGNING_DOMAIN + payload.encode("ascii"), "sha256")
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            data = json.loads(_decode(payload), object_pairs_hook=self._unique_object)
            if not isinstance(data, dict) or set(data) != {"user_id", "expires_at", "csrf_token"}:
                raise ValueError
            expiry = data["expires_at"]
            if (
                type(expiry) is not int
                or not now.timestamp() < expiry <= int(now.timestamp()) + SESSION_SECONDS
            ):
                raise ValueError
            user_id = UUID(data["user_id"])
            if str(user_id) != data["user_id"]:
                raise ValueError
            _decode(data["csrf_token"], 32)
            return SessionClaims(user_id, datetime.fromtimestamp(expiry, UTC), data["csrf_token"])
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError, OverflowError):
            raise InvalidSession("Invalid administrator session") from None


class LoginLimiter:
    """One API process; attempts and KDF concurrency reserved under one lock."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        peer_limit: int = 5,
        total_limit: int = 30,
        max_peers: int = 1024,
    ) -> None:
        self._now = monotonic
        self._peer_limit, self._total_limit, self._max_peers = peer_limit, total_limit, max_peers
        self._lock = Lock()
        self._peers: dict[str, deque[float]] = {}
        self._total: deque[float] = deque()
        self._busy = False

    @contextmanager
    def reserve(self, peer: str) -> Iterator[None]:
        with self._lock:
            now = self._now()
            for key, attempts in list(self._peers.items()):
                while attempts and attempts[0] <= now - 60:
                    attempts.popleft()
                if not attempts:
                    del self._peers[key]
            while self._total and self._total[0] <= now - 60:
                self._total.popleft()
            peer = peer if len(peer) <= 128 else "unknown"
            attempts = self._peers.get(peer, deque())
            if (
                self._busy
                or len(attempts) >= self._peer_limit
                or len(self._total) >= self._total_limit
                or (peer not in self._peers and len(self._peers) >= self._max_peers)
            ):
                raise AppError(429, "login_rate_limited", "Login attempt limit reached")
            self._peers[peer] = attempts
            attempts.append(now)
            self._total.append(now)
            self._busy = True
        try:
            yield
        finally:
            with self._lock:
                self._busy = False
