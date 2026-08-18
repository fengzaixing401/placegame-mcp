import json
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.accounts.service import AccountService
from placegame.contracts import Actor, decode_encrypted_secret, encode_encrypted_secret, encrypted_aad
from placegame.errors import AccountIdentityConflict, InvalidSecret
from placegame.models import ActionPlan, AuditEvent, GameAccount
from placegame.security.crypto import SecretBox
from tests.fakes.game_server import FakeGameApiFactory
from tests.unit.test_accounts import MutableClock, StubPolicyProvider


ADMIN = Actor("webui", "admin")


@dataclass
class LegacyIdentityEnvironment:
    service: AccountService
    sessions: async_sessionmaker
    fake: FakeGameApiFactory
    first_id: UUID
    second_id: UUID
    first_token: str = field(repr=False)
    second_token: str = field(repr=False)
    shared_identity: UUID


@pytest.fixture
async def legacy_identity_env(postgres_url, alembic_config, secret_box: SecretBox):
    config = alembic_config(database_url=postgres_url)
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "001_core")
    first_id = uuid4()
    second_id = uuid4()
    shared_identity = uuid4()
    first_token = f"legacy-credential-token-{shared_identity.hex}"
    second_token = f"legacy-token-{shared_identity.hex}"
    username = f"legacy-user-{shared_identity.hex}"
    password = f"legacy-password-{shared_identity.hex}"
    now = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)

    def seal(value: str, account_id: UUID, column: str) -> bytes:
        return encode_encrypted_secret(
            secret_box.encrypt(
                value,
                aad=encrypted_aad("game_accounts", account_id, column),
            )
        )

    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO game_accounts (
                        id, label, game_username, auth_mode, password,
                        session_token, enabled, policy_version, created_at,
                        updated_at, auth_failure_count
                    ) VALUES (
                        :id, :label, :game_username, :auth_mode, :password,
                        :session_token, true, 1, :created_at, :updated_at, 0
                    )
                    """
                ),
                [
                    {
                        "id": first_id,
                        "label": "legacy credentials",
                        "game_username": seal(username, first_id, "game_username"),
                        "auth_mode": "credentials",
                        "password": seal(password, first_id, "password"),
                        "session_token": seal(first_token, first_id, "session_token"),
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": second_id,
                        "label": "legacy token",
                        "game_username": None,
                        "auth_mode": "token_only",
                        "password": None,
                        "session_token": seal(second_token, second_id, "session_token"),
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
        await asyncio.to_thread(command.upgrade, config, "head")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        fake = FakeGameApiFactory()
        fake.register_credentials(shared_identity, username, password, first_token)
        fake.redirect_token(second_token, first_token)
        service = AccountService(
            sessions,
            secret_box,
            fake,
            policy_provider=StubPolicyProvider(),
            clock=MutableClock(now),
            sleeper=asyncio.sleep,
            jitter=lambda: 0.0,
        )
        yield LegacyIdentityEnvironment(
            service=service,
            sessions=sessions,
            fake=fake,
            first_id=first_id,
            second_id=second_id,
            first_token=first_token,
            second_token=second_token,
            shared_identity=shared_identity,
        )
    finally:
        await engine.dispose()
        await asyncio.to_thread(command.upgrade, config, "head")


@pytest.mark.parametrize("mode", ["token", "credentials"])
async def test_unresolved_historical_row_blocks_new_enrollment(
    legacy_identity_env, mode
):
    environment = legacy_identity_env
    unrelated_id = uuid4()
    unrelated_token = f"unrelated-{unrelated_id.hex}"
    if mode == "token":
        environment.fake.register_token(unrelated_id, unrelated_token)
        enroll = environment.service.add_token_only(
            "new", unrelated_token, actor=ADMIN
        )
    else:
        username = f"unrelated-user-{unrelated_id.hex}"
        password = f"unrelated-password-{unrelated_id.hex}"
        environment.fake.register_credentials(
            unrelated_id, username, password, unrelated_token
        )
        enroll = environment.service.add_credentials(
            "new", username, password, actor=ADMIN
        )
    with pytest.raises(AccountIdentityConflict):
        await enroll
    async with environment.sessions() as session:
        rows = (await session.scalars(select(GameAccount))).all()
        audits = (await session.scalars(select(AuditEvent))).all()
    assert {row.id for row in rows} == {
        environment.first_id,
        environment.second_id,
    }
    rendered = repr([(row.account_id, row.result) for row in audits])
    assert (
        None,
        {
            "status": "rejected",
            "reason": "unresolved_historical_identity",
        },
    ) in [(row.account_id, row.result) for row in audits]
    assert unrelated_token not in rendered
    assert environment.first_token not in rendered
    assert environment.second_token not in rendered


async def test_historical_replacement_binds_stored_identity_before_proposal(
    legacy_identity_env,
):
    environment = legacy_identity_env
    other_id = uuid4()
    other_token = f"other-{other_id.hex}"
    environment.fake.register_token(other_id, other_token)
    async with environment.sessions() as session:
        before = await session.get(GameAccount, environment.first_id)
        assert before is not None
        before_token = before._session_token
    with pytest.raises(AccountIdentityConflict):
        await environment.service.update_token_only(
            environment.first_id, other_token, actor=ADMIN
        )
    async with environment.sessions() as session:
        row = await session.get(GameAccount, environment.first_id)
        audits = (await session.scalars(select(AuditEvent))).all()
    assert row is not None
    assert row.game_account_id == str(environment.shared_identity)
    assert row._session_token == before_token
    rendered = repr([(audit.action, audit.result) for audit in audits])
    assert other_token not in rendered
    assert environment.first_token not in rendered


async def test_concurrent_historical_binding_returns_sanitized_conflict(
    legacy_identity_env,
):
    environment = legacy_identity_env
    results = await asyncio.gather(
        environment.service.update_token_only(
            environment.first_id, environment.first_token, actor=ADMIN
        ),
        environment.service.update_token_only(
            environment.second_id, environment.second_token, actor=ADMIN
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, AccountIdentityConflict) for value in results) == 1
    async with environment.sessions() as session:
        rows = (await session.scalars(select(GameAccount))).all()
        audits = (await session.scalars(select(AuditEvent))).all()
    assert sum(
        row.game_account_id == str(environment.shared_identity) for row in rows
    ) == 1
    rendered = repr(results) + repr(
        [(audit.action, audit.result) for audit in audits]
    )
    assert environment.first_token not in rendered
    assert environment.second_token not in rendered


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


async def test_game_account_secrets_are_record_bound_and_persist_as_binary_frames(
    migrated_engine, secret_box
):
    account = GameAccount(label="encrypted", auth_mode="password")
    account.set_game_username("username", secret_box)
    account.set_password("password", secret_box)
    account.set_session_token("session-token", secret_box)
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(account)
        await session.commit()
        session.expunge_all()
        reloaded = await session.get(GameAccount, account.id)

    async with migrated_engine.connect() as connection:
        stored = await connection.execute(
            text(
                "SELECT game_username, password, session_token "
                "FROM game_accounts WHERE id = :id"
            ),
            {"id": account.id},
        )
        stored_values = stored.one()

    assert reloaded is not None
    assert reloaded.get_game_username(secret_box) == "username"
    assert reloaded.get_password(secret_box) == "password"
    assert reloaded.get_session_token(secret_box) == "session-token"
    assert all(isinstance(value, bytes) and b"password" not in value for value in stored_values)
    assert secret_box.decrypt(
        decode_encrypted_secret(stored_values.password),
        aad=encrypted_aad("game_accounts", account.id, "password"),
    ) == "password"


async def test_game_account_identity_is_unique_when_present(migrated_engine):
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
    identity = f"game-{uuid4()}"

    async with session_factory() as session:
        session.add(
            GameAccount(
                label="first identity",
                auth_mode="token_only",
                game_account_id=identity,
            )
        )
        await session.commit()

        session.add(
            GameAccount(
                label="duplicate identity",
                auth_mode="token_only",
                game_account_id=identity,
            )
        )
        with pytest.raises(DBAPIError):
            await session.commit()


async def test_game_account_rejects_raw_frames_and_detects_aad_copying(migrated_engine, secret_box):
    source = GameAccount(label="source", auth_mode="password")
    source.set_password("password", secret_box)
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(source)
        await session.commit()
        source_id = source.id
        source_password = source._password

        fake_frame = b"\x01" + b"f" * 28
        session.add(
            GameAccount(
                label="fake-frame",
                auth_mode="password",
                _password=fake_frame,
            )
        )
        with pytest.raises(StatementError, match="encrypted secret"):
            await session.commit()
        await session.rollback()

        serialized = encode_encrypted_secret(
            secret_box.encrypt("password", aad=encrypted_aad("game_accounts", source_id, "password"))
        )
        session.add(
            GameAccount(label="serialized-frame", auth_mode="password", _password=serialized)
        )
        with pytest.raises(StatementError, match="encrypted secret"):
            await session.commit()
        await session.rollback()

        copied = GameAccount(label="copied", auth_mode="password")
        copied._password = source_password
        copied._session_token = source_password
        session.add(copied)
        await session.commit()
        copied_id = copied.id
        session.expunge_all()
        reloaded = await session.get(GameAccount, copied_id)

    assert reloaded is not None
    with pytest.raises(InvalidSecret):
        reloaded.get_password(secret_box)
    with pytest.raises(InvalidSecret):
        reloaded.get_session_token(secret_box)


async def test_audit_events_are_redacted_and_reject_updates_and_fresh_deletes(migrated_engine):
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

    with pytest.raises(DBAPIError, match="audit_events are immutable"):
        async with migrated_engine.begin() as connection:
            await connection.execute(
                text("UPDATE audit_events SET action = 'rewritten' WHERE id = :id"), {"id": event_id}
            )
    with pytest.raises(DBAPIError, match="audit_events are retained for 90 days"):
        async with migrated_engine.begin() as connection:
            await connection.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id})


async def test_aged_audit_events_can_be_purged(migrated_engine):
    event_id = uuid4()
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            AuditEvent(
                id=event_id,
                actor="retention",
                source="scheduler",
                action="retention.purge",
                created_at=datetime.now(timezone.utc) - timedelta(days=91),
            )
        )
        await session.commit()

    async with migrated_engine.begin() as connection:
        await connection.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id})


async def test_audit_references_block_parent_deletion_until_aged_event_is_purged(migrated_engine):
    account_id = await _insert_account(migrated_engine)
    plan_id = uuid4()
    fresh_event_id = uuid4()
    aged_account_id = await _insert_account(migrated_engine)
    aged_event_id = uuid4()
    session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(
            ActionPlan(
                id=plan_id,
                account_id=account_id,
                state_fingerprint="state",
                policy_version=1,
                proposed_actions=[],
                estimated_costs={},
                risk="low",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        session.add(
            AuditEvent(
                id=fresh_event_id,
                actor="operator",
                source="mcp",
                account_id=account_id,
                plan_id=plan_id,
                action="plan.execute",
            )
        )
        session.add(
            AuditEvent(
                id=aged_event_id,
                actor="retention",
                source="scheduler",
                account_id=aged_account_id,
                action="retention.purge",
                created_at=datetime.now(timezone.utc) - timedelta(days=91),
            )
        )
        await session.commit()

    with pytest.raises(DBAPIError):
        async with migrated_engine.begin() as connection:
            await connection.execute(text("DELETE FROM action_plans WHERE id = :id"), {"id": plan_id})
    with pytest.raises(DBAPIError):
        async with migrated_engine.begin() as connection:
            await connection.execute(text("DELETE FROM game_accounts WHERE id = :id"), {"id": account_id})

    async with migrated_engine.connect() as connection:
        event = (
            await connection.execute(
                text("SELECT account_id, plan_id FROM audit_events WHERE id = :id"),
                {"id": fresh_event_id},
            )
        ).one()
    assert event.account_id == account_id
    assert event.plan_id == plan_id

    with pytest.raises(DBAPIError):
        async with migrated_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM game_accounts WHERE id = :id"), {"id": aged_account_id}
            )
    async with migrated_engine.begin() as connection:
        await connection.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": aged_event_id})
        await connection.execute(text("DELETE FROM game_accounts WHERE id = :id"), {"id": aged_account_id})


async def test_game_account_policy_version_cannot_decrease(migrated_engine):
    account_id = await _insert_account(migrated_engine)
    async with migrated_engine.begin() as connection:
        await connection.execute(
            text("UPDATE game_accounts SET policy_version = 2 WHERE id = :id"), {"id": account_id}
        )
    with pytest.raises(DBAPIError, match="policy_version cannot decrease"):
        async with migrated_engine.begin() as connection:
            await connection.execute(
                text("UPDATE game_accounts SET policy_version = 1 WHERE id = :id"), {"id": account_id}
            )


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


def test_alembic_config_has_no_static_database_url():
    assert Config("alembic.ini").get_main_option("sqlalchemy.url") is None
