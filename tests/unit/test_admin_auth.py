from datetime import datetime, timedelta, timezone

import pytest

from placegame.models import AdminSession


class MemoryStore:
    def __init__(self) -> None:
        self.password_hash: str | None = None
        self.sessions: dict[str, AdminSession] = {}

    async def setup(self, password_hash: str, now: datetime) -> bool:
        if self.password_hash is not None:
            return False
        self.password_hash = password_hash
        return True

    async def read_password_hash(self) -> str | None:
        return self.password_hash

    async def update_password_hash(self, password_hash: str, now: datetime) -> None:
        self.password_hash = password_hash

    async def create_session(
        self, token_digest: str, now: datetime, absolute_expires_at: datetime
    ) -> AdminSession:
        session = AdminSession(
            token_digest=token_digest,
            created_at=now,
            absolute_expires_at=absolute_expires_at,
            last_seen_at=now,
        )
        self.sessions[token_digest] = session
        return session

    async def find_session(
        self, token_digest: str, now: datetime, idle_seconds: int
    ) -> AdminSession | None:
        session = self.sessions.get(token_digest)
        if session is None:
            return None
        if session.absolute_expires_at <= now or session.last_seen_at + timedelta(seconds=idle_seconds) <= now:
            del self.sessions[token_digest]
            return None
        session.last_seen_at = now
        return session

    async def delete_session(self, token_digest: str) -> None:
        self.sessions.pop(token_digest, None)

    async def delete_all_sessions(self) -> None:
        self.sessions.clear()


@pytest.fixture
def clock():
    class Clock:
        value = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)

        def __call__(self) -> datetime:
            return self.value

    return Clock()


@pytest.fixture
def auth(clock):
    from placegame.admin.auth import AdminAuthService

    return AdminAuthService(MemoryStore(), clock=clock)


async def test_setup_is_one_time_and_only_rejects_a_blank_password(auth):
    from placegame.admin.auth import PasswordTooShort, SetupAlreadyComplete

    with pytest.raises(PasswordTooShort, match="password_too_short"):
        await auth.setup("   ")

    # No minimum length is enforced; a short password is accepted.
    await auth.setup("short")

    with pytest.raises(SetupAlreadyComplete, match="setup_already_complete"):
        await auth.setup("another-password")


async def test_change_password_requires_the_current_password(auth):
    from placegame.admin.auth import Unauthorized

    await auth.setup("first-password")

    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await auth.change_password("wrong-password", "second-password")

    assert await auth.login("first-password") is not None


async def test_change_password_replaces_the_hash_and_drops_every_session(auth):
    from placegame.admin.auth import Unauthorized

    await auth.setup("first-password")
    await auth.login("first-password")
    await auth.login("first-password")
    assert len(auth.store.sessions) == 2

    await auth.change_password("first-password", "x")

    assert auth.store.sessions == {}
    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await auth.login("first-password")
    assert await auth.login("x") is not None


async def test_a_blank_new_password_leaves_the_credential_and_sessions_intact(auth):
    from placegame.admin.auth import PasswordTooShort

    await auth.setup("first-password")
    logged_in = await auth.login("first-password")

    with pytest.raises(PasswordTooShort, match="password_too_short"):
        await auth.change_password("first-password", "   ")

    assert await auth.validate(logged_in.token) is not None
    assert await auth.login("first-password") is not None


async def test_failed_login_is_always_generic(auth):
    from placegame.admin.auth import Unauthorized

    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await auth.login("wrong-password")

    await auth.setup("correct-password")
    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await auth.login("wrong-password")


async def test_login_creates_opaque_twelve_hour_session(auth, clock):
    from placegame.admin.auth import digest_session_token

    await auth.setup("correct-password")
    logged_in = await auth.login("correct-password")

    assert len(logged_in.token) == 43
    assert logged_in.absolute_expires_at == clock.value + timedelta(hours=12)
    assert logged_in.token not in repr(auth.store.sessions)
    assert digest_session_token(logged_in.token) in auth.store.sessions
    assert await auth.validate(logged_in.token) is not None


async def test_expired_session_is_rejected_and_removed(auth, clock):
    from placegame.admin.auth import digest_session_token

    await auth.setup("correct-password")
    logged_in = await auth.login("correct-password")
    digest = digest_session_token(logged_in.token)
    clock.value = logged_in.absolute_expires_at

    assert await auth.validate(logged_in.token) is None
    assert digest not in auth.store.sessions


async def test_idle_session_expiry_is_rejected_and_removed(auth, clock):
    from placegame.admin.auth import digest_session_token

    await auth.setup("correct-password")
    logged_in = await auth.login("correct-password")
    digest = digest_session_token(logged_in.token)
    clock.value = logged_in.created_at + timedelta(minutes=30, seconds=1)

    assert await auth.validate(logged_in.token) is None
    assert digest not in auth.store.sessions


async def test_logout_revokes_only_the_presented_session(auth):
    await auth.setup("correct-password")
    first = await auth.login("correct-password")
    second = await auth.login("correct-password")

    await auth.logout(first.token)

    assert await auth.validate(first.token) is None
    assert await auth.validate(second.token) is not None


async def test_blank_or_malformed_session_tokens_are_unauthenticated(auth):
    assert await auth.validate(None) is None
    assert await auth.validate("") is None
    assert await auth.validate("not-a-session-token") is None
