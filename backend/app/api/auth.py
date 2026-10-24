"""Access is deliberately denied until administrator sessions are implemented in A2."""

from typing import NoReturn

from app.contracts.errors import AppError


def require_admin() -> NoReturn:
    raise AppError(503, "auth_not_configured", "Administrator authentication is not configured")
