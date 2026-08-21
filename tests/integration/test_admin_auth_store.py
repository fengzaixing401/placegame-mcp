from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from placegame.admin.auth import (
    AdminAuthService,
    PostgresAdminAuthStore,
    SetupAlreadyComplete,
    Unauthorized,
    digest_session_token,
)
from placegame.models import AdminCredential, AdminSession


pytestmark = pytest.mark.integration


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def admin_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def sessions(admin_database_url):
    engine = create_async_engine(admin_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE admin_sessions, admin_credentials "
                "RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


def build_service(
    factory: async_sessionmaker[AsyncSession], *, now: datetime = NOW
) -> AdminAuthService:
    return AdminAuthService(PostgresAdminAuthStore(factory), clock=lambda: now)


async def read_credential(
    factory: async_sessionmaker[AsyncSession],
) -> AdminCredential | None:
    async with factory() as session:
        return await session.scalar(select(AdminCredential))


async def read_sessions(
    factory: async_sessionmaker[AsyncSession],
) -> list[AdminSession]:
    async with factory() as session:
        return list((await session.scalars(select(AdminSession))).all())


async def test_setup_writes_one_singleton_row_and_rejects_a_second_attempt(sessions):
    store = PostgresAdminAuthStore(sessions)

    assert await store.read_password_hash() is None
    assert await store.setup("first-hash", NOW) is True

    credential = await read_credential(sessions)
    assert credential is not None
    assert credential.id == 1
    assert credential.password_hash == "first-hash"
    assert await store.read_password_hash() == "first-hash"

    with pytest.raises(SetupAlreadyComplete, match="setup_already_complete"):
        await store.setup("second-hash", NOW)

    assert await store.read_password_hash() == "first-hash"


async def test_update_password_hash_replaces_the_hash_and_stamps_updated_at(sessions):
    store = PostgresAdminAuthStore(sessions)
    await store.setup("first-hash", NOW)
    later = NOW + timedelta(minutes=5)

    await store.update_password_hash("second-hash", later)

    credential = await read_credential(sessions)
    assert credential is not None
    assert credential.password_hash == "second-hash"
    assert credential.created_at == NOW
    assert credential.updated_at == later
    assert await store.read_password_hash() == "second-hash"


async def test_update_password_hash_without_a_credential_is_unauthorized(sessions):
    store = PostgresAdminAuthStore(sessions)

    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await store.update_password_hash("orphan-hash", NOW)

    assert await read_credential(sessions) is None


async def test_delete_all_sessions_removes_every_row(sessions):
    store = PostgresAdminAuthStore(sessions)
    for index in range(3):
        await store.create_session(
            f"{index:064d}", NOW, NOW + timedelta(hours=12)
        )
    assert len(await read_sessions(sessions)) == 3

    await store.delete_all_sessions()

    assert await read_sessions(sessions) == []


async def test_delete_all_sessions_on_an_empty_table_is_a_no_op(sessions):
    await PostgresAdminAuthStore(sessions).delete_all_sessions()

    assert await read_sessions(sessions) == []


async def test_change_password_against_postgres_rotates_the_hash_and_drops_sessions(
    sessions,
):
    service = build_service(sessions)
    await service.setup("first-password")
    first = await service.login("first-password")
    second = await service.login("first-password")
    assert len({first.token, second.token}) == 2
    assert len(await read_sessions(sessions)) == 2

    await service.change_password("first-password", "x")

    assert await read_sessions(sessions) == []
    assert await service.validate(first.token) is None
    assert await service.validate(second.token) is None
    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await service.login("first-password")

    reissued = await service.login("x")
    stored = await read_sessions(sessions)
    assert [record.token_digest for record in stored] == [
        digest_session_token(reissued.token)
    ]


async def test_a_wrong_current_password_changes_nothing_in_postgres(sessions):
    service = build_service(sessions)
    await service.setup("first-password")
    logged_in = await service.login("first-password")
    before = await read_credential(sessions)
    assert before is not None

    with pytest.raises(Unauthorized, match="^unauthorized$"):
        await service.change_password("wrong-password", "second-password")

    after = await read_credential(sessions)
    assert after is not None
    assert after.password_hash == before.password_hash
    assert after.updated_at == before.updated_at
    assert await service.validate(logged_in.token) is not None
