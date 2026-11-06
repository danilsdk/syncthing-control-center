"""Explicit single-administrator bootstrap and bounded SQL user lookup."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text, update

from app.db import Database
from app.models import AppUser
from app.registry.security import PasswordHasher


@dataclass(frozen=True)
class AdminRecord:
    id: UUID
    login: str
    password_hash: str = field(repr=False)
    is_active: bool = True


class BootstrapError(ValueError):
    pass


class AdminStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def administrator(self) -> AdminRecord | None:
        with self.database.session() as session:
            rows = session.scalars(select(AppUser).limit(2)).all()
            if len(rows) != 1:
                return None
            user = rows[0]
            return AdminRecord(user.id, user.login, user.password_hash, user.is_active)

    def mark_login(self, admin: AdminRecord, now: datetime) -> bool:
        with self.database.session() as session:
            result = session.execute(
                update(AppUser)
                .where(
                    AppUser.id == admin.id,
                    AppUser.is_active.is_(True),
                    AppUser.password_hash == admin.password_hash,
                )
                .values(last_login_at=now)
            )
            if result.rowcount != 1:
                return False
            session.commit()
            return True


def create_administrator(database: Database, login: str, password: str) -> UUID:
    normalized = login.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{1,64}", normalized):
        raise BootstrapError(
            "Administrator login must contain ASCII letters, digits, dot, dash or underscore"
        )
    encoded = PasswordHasher().hash(password)
    with database.session() as session:
        # Serializes different logins too; login's unique index alone is insufficient.
        session.execute(text("LOCK TABLE app_users IN SHARE ROW EXCLUSIVE MODE"))
        if session.scalar(select(AppUser.id).limit(1)) is not None:
            raise BootstrapError("An administrator already exists; no user was changed")
        user = AppUser(login=normalized, password_hash=encoded, is_active=True)
        session.add(user)
        session.flush()
        identifier = user.id
        session.commit()
        return identifier
