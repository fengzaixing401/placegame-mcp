from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.models import AdminCredential, AdminSession


@pytest.fixture
async def admin_engine(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    engine = create_async_engine(postgres_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_admin_models_define_singleton_and_digest_constraints():
    credential = AdminCredential.__table__
    session = AdminSession.__table__

    assert credential.name == "admin_credentials"
    assert session.name == "admin_sessions"
    assert any(constraint.name == "ck_admin_credentials_singleton" for constraint in credential.constraints)
    assert any(constraint.name == "uq_admin_sessions_token_digest" for constraint in session.constraints)


@pytest.mark.integration
async def test_admin_credential_is_singleton_and_session_digest_is_unique(admin_engine):
    sessions = async_sessionmaker(admin_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    digest = "a" * 64

    async with sessions.begin() as db:
        db.add(AdminCredential(password_hash="argon2id-hash", created_at=now, updated_at=now))
        db.add(
            AdminSession(
                token_digest=digest,
                created_at=now,
                expires_at=now,
                last_seen_at=now,
            )
        )

    async with sessions.begin() as db:
        db.add(AdminCredential(password_hash="second", created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            await db.flush()

    async with sessions.begin() as db:
        db.add(
            AdminSession(
                token_digest=digest,
                created_at=now,
                expires_at=now,
                last_seen_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()

    async with sessions() as db:
        assert await db.scalar(select(AdminCredential.password_hash)) == "argon2id-hash"
        assert await db.scalar(select(AdminSession.token_digest)) == digest
