import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.contracts import decode_encrypted_secret, encode_encrypted_secret, encrypted_aad
from placegame.models import AuditEvent, GameAccount


def test_migrations_upgrade_and_match_metadata(postgres_url, alembic_config):
    config = alembic_config(database_url=postgres_url)

    command.upgrade(config, "head")
    command.check(config)


@pytest.fixture
def migrated_postgres_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(database_url=postgres_url), "head")
    return postgres_url


@pytest.fixture
async def migrated_engine(migrated_postgres_url):
    engine = create_async_engine(migrated_postgres_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert_account(engine) -> UUID:
    account_id = uuid4()
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO game_accounts (
                    id, label, auth_mode, enabled, policy_version, created_at, updated_at,
                    auth_failure_count
                ) VALUES (
                    :id, :label, :auth_mode, :enabled, :policy_version, :created_at,
                    :updated_at, :auth_failure_count
                )
                """
            ),
            {
                "id": account_id,
                "label": "integration",
                "auth_mode": "password",
                "enabled": True,
                "policy_version": 1,
                "created_at": now,
                "updated_at": now,
                "auth_failure_count": 0,
            },
        )
    return account_id


async def test_game_account_persists_only_framed_encrypted_secrets(migrated_engine, secret_box):
    account_id = uuid4()
    aad = encrypted_aad("game_accounts", account_id, "password")
    framed = encode_encrypted_secret(secret_box.encrypt("password", aad=aad))
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(
            GameAccount(
                id=account_id,
                label="encrypted",
                auth_mode="password",
                password=framed,
            )
        )
        await session.commit()

    async with migrated_engine.connect() as connection:
        stored = await connection.scalar(
            text("SELECT password FROM game_accounts WHERE id = :id"), {"id": account_id}
        )

    assert stored == framed
    assert secret_box.decrypt(decode_encrypted_secret(stored), aad=aad) == "password"

    async with session_factory() as session:
        session.add(
            GameAccount(
                id=uuid4(),
                label="plaintext",
                auth_mode="password",
                password=b"plaintext",
            )
        )
        with pytest.raises(StatementError, match="encrypted secret format"):
            await session.commit()


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE audit_events SET action = 'rewritten' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
    ),
)
async def test_audit_events_are_redacted_and_immutable(migrated_engine, statement):
    event_id = uuid4()
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(
            AuditEvent(
                id=event_id,
                actor="operator",
                source="mcp",
                action="account.update",
                before={"nested": {"token": "do-not-store", "note": "x" * 300}},
                after={"Authorization": "Bearer do-not-store"},
            )
        )
        await session.commit()
        stored_before = await session.scalar(select(AuditEvent.before).where(AuditEvent.id == event_id))
        stored_after = await session.scalar(select(AuditEvent.after).where(AuditEvent.id == event_id))

    assert stored_before == {
        "nested": {"token": "[REDACTED]", "note": "x" * 256 + "...[TRUNCATED]"}
    }
    assert stored_after == {"Authorization": "[REDACTED]"}

    with pytest.raises(DBAPIError, match="audit_events are append-only"):
        async with migrated_engine.begin() as connection:
            await connection.execute(text(statement), {"id": event_id})


async def test_snapshot_expiry_is_exactly_five_minutes(migrated_engine):
    account_id = await _insert_account(migrated_engine)
    fetched_at = datetime.now(timezone.utc)
    statement = text(
        """
        INSERT INTO account_snapshots (
            id, account_id, sanitized_state, state_fingerprint, fetched_at, expires_at
        ) VALUES (
            :id, :account_id, CAST(:sanitized_state AS jsonb), :state_fingerprint,
            :fetched_at, :expires_at
        )
        """
    )
    values = {
        "id": uuid4(),
        "account_id": account_id,
        "sanitized_state": json.dumps({"safe": True}),
        "state_fingerprint": "state",
        "fetched_at": fetched_at,
        "expires_at": fetched_at + timedelta(minutes=4),
    }

    with pytest.raises(DBAPIError, match="snapshot_expiry"):
        async with migrated_engine.begin() as connection:
            await connection.execute(statement, values)

    values["id"] = uuid4()
    values["expires_at"] = fetched_at + timedelta(minutes=5)
    async with migrated_engine.begin() as connection:
        await connection.execute(statement, values)


async def test_scheduler_lease_is_seeded_and_only_allows_default_name(migrated_engine):
    async with migrated_engine.connect() as connection:
        names = (await connection.scalars(text("SELECT name FROM scheduler_leases"))).all()

    assert names == ["default"]
    with pytest.raises(DBAPIError, match="scheduler_leases_default_name"):
        async with migrated_engine.begin() as connection:
            await connection.execute(text("INSERT INTO scheduler_leases (name, updated_at) VALUES ('other', now())"))


async def test_account_policy_rejects_non_object_json(migrated_engine):
    account_id = await _insert_account(migrated_engine)
    with pytest.raises(DBAPIError, match="account_policies_policy_object"):
        async with migrated_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO account_policies (account_id, policy, policy_version, created_at, updated_at)
                    VALUES (:account_id, CAST(:policy AS jsonb), 1, now(), now())
                    """
                ),
                {"account_id": account_id, "policy": json.dumps([])},
            )


def test_migrations_read_database_url_from_secret_file(postgres_url, monkeypatch, tmp_path):
    secret_file = tmp_path / "database-url"
    secret_file.write_text(f"{postgres_url}\n", encoding="utf-8")
    monkeypatch.setenv("PLACEGAME_DATABASE_URL_FILE", str(secret_file))
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.check(config)
