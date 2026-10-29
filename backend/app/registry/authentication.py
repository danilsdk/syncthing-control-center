"""Authentication without framework objects or Syncthing calls."""

from app.config import Settings
from app.contracts.dto import Session, User
from app.contracts.errors import AppError
from app.contracts.interfaces import Clock
from app.db import DatabaseNotReady
from app.registry.admin import AdminRecord, AdminStore
from app.registry.security import (
    InvalidSession,
    LoginLimiter,
    PasswordHasher,
    PasswordHashError,
    SessionCodec,
)


class AdministratorAuth:
    def __init__(
        self,
        settings: Settings,
        store: AdminStore,
        clock: Clock,
        *,
        hasher: PasswordHasher | None = None,
        limiter: LoginLimiter | None = None,
    ) -> None:
        self.settings, self.store, self.clock = settings, store, clock
        self.hasher = hasher if hasher is not None else PasswordHasher()
        self.limiter = limiter if limiter is not None else LoginLimiter()
        self._dummy = self.hasher.dummy_hash()

    def _codec(self) -> SessionCodec:
        if self.settings.session_secret is None:
            raise AppError(
                503, "auth_not_configured", "Administrator authentication is not configured"
            )
        return SessionCodec(self.settings.session_secret.get_secret_value())

    def _admin(self) -> AdminRecord:
        try:
            admin = self.store.administrator()
        except DatabaseNotReady:
            raise AppError(503, "service_unavailable", "Database is unavailable") from None
        if admin is None:
            raise AppError(
                503, "auth_not_configured", "Administrator authentication is not configured"
            )
        return admin

    @staticmethod
    def _session(admin: AdminRecord, claims) -> Session:
        return Session(
            user=User(id=admin.id, login=admin.login),
            csrf_token=claims.csrf_token,
            expires_at=claims.expires_at,
        )

    def login(self, login: str, password: str, peer: str) -> tuple[str, Session]:
        codec = self._codec()
        with self.limiter.reserve(peer):
            admin = self._admin()
            matches = login.lower() == admin.login
            try:
                self.hasher.validate(admin.password_hash)
                valid = self.hasher.verify(
                    password, admin.password_hash if matches else self._dummy
                )
            except (PasswordHashError, MemoryError, ValueError):
                raise AppError(
                    503, "auth_not_configured", "Administrator authentication is unavailable"
                ) from None
            if not matches or not valid or not admin.is_active:
                raise AppError(401, "invalid_credentials", "Invalid administrator credentials")
            try:
                recorded = self.store.mark_login(admin, self.clock.now_utc())
            except DatabaseNotReady:
                raise AppError(503, "service_unavailable", "Database is unavailable") from None
            if not recorded:
                raise AppError(401, "invalid_credentials", "Invalid administrator credentials")
            token, claims = codec.issue(admin.id, self.clock.now_utc())
            return token, self._session(admin, claims)

    def authenticate(self, token: str | None) -> Session:
        codec = self._codec()
        admin = self._admin()
        try:
            claims = codec.read(token, self.clock.now_utc())
        except InvalidSession:
            raise AppError(
                401, "authentication_required", "Administrator session is required"
            ) from None
        if not admin.is_active or admin.id != claims.user_id:
            raise AppError(401, "authentication_required", "Administrator session is required")
        return self._session(admin, claims)
