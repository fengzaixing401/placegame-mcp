from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from placegame.models import AdminCredential, AdminSession
from placegame.security.tokens import token_digest


SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
DEFAULT_IDLE_SECONDS = 30 * 60
DEFAULT_ABSOLUTE_SECONDS = 12 * 60 * 60


class AdminAuthError(RuntimeError):
    """Stable, non-sensitive administrator authentication error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PasswordTooShort(AdminAuthError):
    def __init__(self) -> None:
        super().__init__("password_too_short")


class SetupAlreadyComplete(AdminAuthError):
    def __init__(self) -> None:
        super().__init__("setup_already_complete")


class Unauthorized(AdminAuthError):
    def __init__(self) -> None:
        super().__init__("unauthorized")


@dataclass(frozen=True)
class LoginSession:
    token: str = field(repr=False)
    session: AdminSession

    @property
    def id(self):
        return self.session.id

    @property
    def created_at(self) -> datetime:
        return self.session.created_at

    @property
    def absolute_expires_at(self) -> datetime:
        return self.session.absolute_expires_at


class AdminAuthStore(Protocol):
    async def setup(self, password_hash: str, now: datetime) -> bool: ...

    async def read_password_hash(self) -> str | None: ...

    async def update_password_hash(self, password_hash: str, now: datetime) -> None: ...

    async def create_session(
        self, token_digest: str, now: datetime, absolute_expires_at: datetime
    ) -> AdminSession: ...

    async def find_session(
        self, token_digest: str, now: datetime, idle_seconds: int
    ) -> AdminSession | None: ...

    async def delete_session(self, token_digest: str) -> None: ...

    async def delete_all_sessions(self) -> None: ...


class PostgresAdminAuthStore:
    """Persistence adapter for the singleton administrator and its sessions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def setup(self, password_hash: str, now: datetime) -> bool:
        try:
            async with self.sessions.begin() as session:
                session.add(
                    AdminCredential(
                        id=1,
                        password_hash=password_hash,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                return True
        except IntegrityError:
            # The singleton primary key is the concurrency gate. The failed
            # transaction is rolled back before exposing a stable error.
            raise SetupAlreadyComplete() from None

    async def read_password_hash(self) -> str | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(AdminCredential.password_hash).where(AdminCredential.id == 1)
            )

    async def update_password_hash(self, password_hash: str, now: datetime) -> None:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(AdminCredential)
                .where(AdminCredential.id == 1)
                .with_for_update()
            )
            if record is None:
                raise Unauthorized()
            record.password_hash = password_hash
            record.updated_at = now
            await session.flush()

    async def create_session(
        self, token_digest: str, now: datetime, absolute_expires_at: datetime
    ) -> AdminSession:
        async with self.sessions.begin() as session:
            record = AdminSession(
                token_digest=token_digest,
                created_at=now,
                absolute_expires_at=absolute_expires_at,
                last_seen_at=now,
            )
            session.add(record)
            await session.flush()
            return record

    async def find_session(
        self, token_digest: str, now: datetime, idle_seconds: int
    ) -> AdminSession | None:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(AdminSession)
                .where(AdminSession.token_digest == token_digest)
                .with_for_update()
            )
            if record is None:
                return None
            idle_expiry = record.last_seen_at + timedelta(seconds=idle_seconds)
            if now >= record.absolute_expires_at or now >= idle_expiry:
                await session.delete(record)
                return None
            record.last_seen_at = now
            await session.flush()
            return record

    async def delete_session(self, token_digest: str) -> None:
        async with self.sessions.begin() as session:
            record = await session.scalar(
                select(AdminSession)
                .where(AdminSession.token_digest == token_digest)
                .with_for_update()
            )
            if record is not None:
                await session.delete(record)

    async def delete_all_sessions(self) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(AdminSession))


class AdminAuthService:
    def __init__(
        self,
        store: AdminAuthStore,
        *,
        clock: Callable[[], datetime] | None = None,
        idle_seconds: int = DEFAULT_IDLE_SECONDS,
        absolute_seconds: int = DEFAULT_ABSOLUTE_SECONDS,
        hasher: PasswordHasher | None = None,
    ) -> None:
        if idle_seconds <= 0 or absolute_seconds <= 0:
            raise ValueError("session lifetimes must be positive")
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.idle_seconds = idle_seconds
        self.absolute_seconds = absolute_seconds
        self.hasher = hasher or PasswordHasher()

    async def is_setup(self) -> bool:
        return await self.store.read_password_hash() is not None

    async def setup(self, password: str) -> None:
        self._require_password(password)
        password_hash = self.hasher.hash(password)
        created = await self.store.setup(password_hash, self._now())
        if created is False:
            raise SetupAlreadyComplete()

    async def login(self, password: str) -> LoginSession:
        password_hash = await self.store.read_password_hash()
        if password_hash is None or not isinstance(password, str):
            raise Unauthorized()
        try:
            self.hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            raise Unauthorized() from None
        now = self._now()
        token = secrets.token_urlsafe(32)
        record = await self.store.create_session(
            token_digest(token),
            now,
            now + timedelta(seconds=self.absolute_seconds),
        )
        return LoginSession(token=token, session=record)

    async def change_password(self, current_password: str, new_password: str) -> None:
        password_hash = await self.store.read_password_hash()
        if password_hash is None or not isinstance(current_password, str):
            raise Unauthorized()
        try:
            self.hasher.verify(password_hash, current_password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            raise Unauthorized() from None
        # Checked only after the current password is proven, so a caller who does not
        # know it cannot tell a rejected new password from a rejected old one.
        self._require_password(new_password)
        await self.store.update_password_hash(
            self.hasher.hash(new_password), self._now()
        )
        # Every session is dropped so a stolen cookie cannot outlive the change.
        await self.store.delete_all_sessions()

    async def validate(self, raw_token: str | None) -> AdminSession | None:
        if not isinstance(raw_token, str) or SESSION_TOKEN_PATTERN.fullmatch(raw_token) is None:
            return None
        return await self.store.find_session(
            token_digest(raw_token), self._now(), self.idle_seconds
        )

    async def logout(self, raw_token: str | None) -> None:
        if not isinstance(raw_token, str) or SESSION_TOKEN_PATTERN.fullmatch(raw_token) is None:
            return
        await self.store.delete_session(token_digest(raw_token))

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("admin authentication clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _require_password(password: str) -> None:
        if not isinstance(password, str) or not password.strip():
            raise PasswordTooShort()


def digest_session_token(token: str) -> str:
    """Expose the shared digest helper for tests and storage diagnostics."""

    return token_digest(token)
