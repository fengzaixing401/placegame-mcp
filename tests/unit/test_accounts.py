from __future__ import annotations

import asyncio
import base64
import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.accounts.service import (
    AccountService,
    MutationOutcome,
    default_token_expiry,
)
from placegame.contracts import Actor
from placegame.errors import (
    AccountDisabled,
    AccountPaused,
    AuthenticationRequired,
    GameConflict,
    PlanPreconditionFailed,
    PolicyUnavailable,
    ReconciliationRequired,
)
from placegame.models import AuditEvent, GameAccount, Job
from tests.fakes.game_server import FakeGameApiFactory


if TYPE_CHECKING:
    from placegame.accounts.service import VersionedPolicy


ADMIN = Actor("webui", "admin")
SCHEDULER = Actor("scheduler", "scheduler-1")


@dataclass
class StubPolicy:
    account_id: UUID
    version: int = 1


class StubPolicyProvider:
    def __init__(self) -> None:
        self.requested: list[UUID] = []

    async def get(self, account_id: UUID) -> VersionedPolicy:
        self.requested.append(account_id)
        return StubPolicy(account_id)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class ServiceEnvironment:
    def __init__(self, sessions, secret_box, engine) -> None:
        self.sessions = sessions
        self.engine = engine
        self.fake = FakeGameApiFactory()
        self.policy = StubPolicyProvider()
        self.expiries: dict[str, datetime | None] = {}
        self.clock = MutableClock(datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc))
        self.delays: list[float] = []
        self.service = AccountService(
            sessions,
            secret_box,
            self.fake,
            policy_provider=self.policy,
            token_expiry=self.expiries.get,
            clock=self.clock,
            sleeper=self._sleep,
            jitter=lambda: 0.0,
        )

    async def _sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)

    async def add_token(
        self,
        label: str = "token account",
        *,
        expires_at: datetime | None = None,
    ):
        registered_id = uuid4()
        token = f"test-token-{registered_id.hex}"
        self.fake.register_token(registered_id, token)
        self.expiries[token] = expires_at
        account = await self.service.add_token_only(label, token, actor=ADMIN)
        self.fake.bind_account_id(registered_id, account.id)
        return account, token

    async def add_credentials(
        self,
        label: str = "credential account",
        *,
        username: str | None = None,
        password: str | None = None,
        expires_at: datetime | None = None,
    ):
        registered_id = uuid4()
        username = username or f"user-{registered_id.hex}"
        password = password or f"password-{registered_id.hex}"
        token = f"credential-token-{registered_id.hex}"
        self.fake.register_credentials(registered_id, username, password, token)
        self.expiries[token] = expires_at
        account = await self.service.add_credentials(label, username, password, actor=ADMIN)
        self.fake.bind_account_id(registered_id, account.id)
        return account, username, password, token


@pytest.fixture
def account_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def account_env(account_database_url, secret_box):
    engine = create_async_engine(account_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE audit_events, job_runs, jobs, action_plans, "
                "account_snapshots, account_policies, game_accounts RESTART IDENTITY CASCADE"
            )
        )
    environment = ServiceEnvironment(sessions, secret_box, engine)
    try:
        yield environment
    finally:
        await engine.dispose()


def _jwt(exp: object) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp})}.signature"


def test_public_mutation_contract_keeps_none_honest():
    verifier = inspect.signature(AccountService.mutate).parameters["verify"].annotation

    assert "T | None" in str(verifier)
    assert "T | None" in str(MutationOutcome.__annotations__["result"])


def test_public_locked_contract_returns_an_async_context_manager():
    return_type = inspect.signature(AccountService.locked).return_annotation

    assert "AbstractAsyncContextManager" in str(return_type)


def test_default_token_expiry_is_bounded_and_never_fabricates_expiry():
    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)

    assert default_token_expiry(_jwt(expiry.timestamp())) == expiry
    assert default_token_expiry("opaque-token") is None
    assert default_token_expiry(_jwt(True)) is None
    assert default_token_expiry("x" * 9000) is None


async def test_added_accounts_are_verified_sanitized_and_opaque_tokens_stay_none(account_env):
    token_account, _ = await account_env.add_token("  Token Label  ")
    credential_account, _, _, _ = await account_env.add_credentials(" Credentials ")

    assert token_account.label == "Token Label"
    assert token_account.session_expires_at is None
    assert credential_account.label == "Credentials"
    assert not hasattr(token_account, "session_token")
    assert not hasattr(credential_account, "password")
    assert account_env.fake.bootstrap_count(token_account.id) == 1
    assert account_env.fake.bootstrap_count(credential_account.id) == 1


@pytest.mark.parametrize("label", ["", "   ", "x" * 121])
async def test_labels_are_non_empty_and_at_most_120_characters(account_env, label):
    registered_id = uuid4()
    token = f"label-token-{registered_id.hex}"
    account_env.fake.register_token(registered_id, token)

    with pytest.raises(ValueError, match="label"):
        await account_env.service.add_token_only(label, token, actor=ADMIN)


async def test_credentials_renew_when_expiry_is_within_24_hours(account_env):
    account, _, _, _ = await account_env.add_credentials(
        expires_at=account_env.clock() + timedelta(days=7)
    )
    initial_login_count = account_env.fake.login_count
    async with account_env.sessions.begin() as session:
        record = await session.get(GameAccount, account.id)
        record.session_expires_at = account_env.clock() + timedelta(hours=23)

    state = await account_env.service.ensure_session(account.id, actor=SCHEDULER)

    assert state.authenticated is True
    assert state.refreshed is True
    assert account_env.fake.login_count == initial_login_count + 1


async def test_credentials_renew_when_token_is_absent_or_rejected(account_env):
    absent, _, _, _ = await account_env.add_credentials("absent")
    rejected, _, _, _ = await account_env.add_credentials("rejected")
    async with account_env.sessions.begin() as session:
        record = await session.get(GameAccount, absent.id)
        record.set_session_token(None, account_env.service.secret_box)
        record.session_expires_at = None
    account_env.fake.reject_account_session(rejected.id)

    absent_state = await account_env.service.ensure_session(absent.id, actor=SCHEDULER)
    rejected_state = await account_env.service.ensure_session(rejected.id, actor=SCHEDULER)

    assert absent_state.refreshed is True
    assert rejected_state.refreshed is True


async def test_token_only_near_expiry_pauses_only_itself(account_env):
    expiring, _ = await account_env.add_token(
        "expiring", expires_at=account_env.clock() + timedelta(hours=1)
    )
    healthy, _ = await account_env.add_token(
        "healthy", expires_at=account_env.clock() + timedelta(days=7)
    )

    state = await account_env.service.ensure_session(expiring.id, actor=SCHEDULER)

    assert state.authenticated is False
    assert state.paused_reason == "session_refresh_required"
    assert (await account_env.service.get(expiring.id)).paused_reason == "session_refresh_required"
    assert (await account_env.service.get(healthy.id)).paused_reason is None
    async with account_env.sessions() as session:
        audit = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.account_id == expiring.id)
            )
        ).all()
    assert any(row.result == {"status": "paused", "severity": "critical"} for row in audit)


async def test_rejected_opaque_token_pauses_without_inventing_expiry(account_env):
    account, _ = await account_env.add_token()
    account_env.fake.reject_account_session(account.id)

    state = await account_env.service.ensure_session(account.id, actor=SCHEDULER)

    assert state.authenticated is False
    assert state.expires_at is None
    assert state.paused_reason == "session_refresh_required"


async def test_locked_context_commits_refresh_pause_before_raising(account_env):
    account, _ = await account_env.add_token(
        expires_at=account_env.clock() + timedelta(hours=1)
    )

    with pytest.raises(AuthenticationRequired):
        async with account_env.service.locked(account.id):
            pytest.fail("an unauthenticated account must not enter the locked context")

    managed = await account_env.service.get(account.id)
    assert managed.paused_reason == "session_refresh_required"


async def test_three_failed_credential_cycles_in_one_hour_pause_only_that_account(account_env):
    failing, _, password, _ = await account_env.add_credentials("failing")
    healthy, _, _, _ = await account_env.add_credentials("healthy")
    account_env.fake.reject_account_session(failing.id)
    account_env.fake.reject_login(password)

    first = await account_env.service.ensure_session(failing.id, actor=SCHEDULER)
    second = await account_env.service.ensure_session(failing.id, actor=SCHEDULER)
    third = await account_env.service.ensure_session(failing.id, actor=SCHEDULER)

    assert first.paused_reason is None
    assert second.paused_reason is None
    assert third.paused_reason == "authentication_required"
    assert (await account_env.service.get(healthy.id)).paused_reason is None
    assert account_env.delays[-6:] == [1.0, 2.0, 1.0, 2.0, 1.0, 2.0]


async def test_credential_update_is_verified_before_old_secret_is_replaced(account_env):
    account, _, _, _ = await account_env.add_credentials()
    marker = "bad-password-never-persist"
    account_env.fake.reject_login(marker)
    async with account_env.sessions() as session:
        before = (await session.get(GameAccount, account.id))._password

    with pytest.raises(AuthenticationRequired):
        await account_env.service.update_credentials(account.id, None, marker, actor=ADMIN)

    async with account_env.sessions() as session:
        after = (await session.get(GameAccount, account.id))._password
    snapshot = await account_env.service.snapshot(account.id, actor=ADMIN)
    assert after == before
    assert snapshot.authenticated is True
    assert marker not in repr(after)


async def test_token_update_is_verified_before_old_secret_is_replaced(account_env):
    account, _ = await account_env.add_token()
    marker = "rejected-token-never-persist"
    async with account_env.sessions() as session:
        before = (await session.get(GameAccount, account.id))._session_token

    with pytest.raises(AuthenticationRequired):
        await account_env.service.update_token_only(account.id, marker, actor=ADMIN)

    async with account_env.sessions() as session:
        after = (await session.get(GameAccount, account.id))._session_token
    assert after == before
    assert marker not in repr(after)


async def test_label_and_lifecycle_changes_are_audited_without_secrets(account_env):
    account, token = await account_env.add_token()

    updated = await account_env.service.update_label(account.id, "  renamed  ", actor=ADMIN)
    await account_env.service.disable(account.id, actor=ADMIN)
    await account_env.service.enable(account.id, actor=ADMIN)
    await account_env.service.pause(account.id, "operator", actor=ADMIN)
    await account_env.service.resume(account.id, actor=ADMIN)

    assert updated.label == "renamed"
    async with account_env.sessions() as session:
        audits = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.account_id == account.id)
            )
        ).all()
    rendered = repr([(row.action, row.result, row.before, row.after) for row in audits])
    assert {"account.label.update", "account.disable", "account.enable", "account.pause", "account.resume"}.issubset(
        {row.action for row in audits}
    )
    assert token not in rendered


async def test_disable_drain_remove_clears_secrets_disables_jobs_and_keeps_tombstone(account_env):
    account, _, _, _ = await account_env.add_credentials()
    async with account_env.sessions.begin() as session:
        session.add(
            Job(
                account_id=account.id,
                kind="idle",
                schedule="0 * * * *",
                timezone="Asia/Shanghai",
                enabled=True,
                misfire_policy="defer",
            )
        )

    receipt = await account_env.service.disable_drain_remove(account.id, actor=ADMIN)

    assert receipt.account_id == account.id
    assert receipt.disabled_job_count == 1
    removed = await account_env.service.get(account.id)
    assert removed.enabled is False
    assert removed.paused_reason == "removed"
    async with account_env.sessions() as session:
        record = await session.get(GameAccount, account.id)
        job = await session.scalar(select(Job).where(Job.account_id == account.id))
    assert record._game_username is None
    assert record._password is None
    assert record._session_token is None
    assert job.enabled is False


async def test_default_policy_provider_fails_closed_before_write(account_env):
    account, _ = await account_env.add_token()
    service = AccountService(
        account_env.sessions,
        account_env.service.secret_box,
        account_env.fake,
        token_expiry=account_env.expiries.get,
        clock=account_env.clock,
        sleeper=account_env._sleep,
        jitter=lambda: 0.0,
    )

    with pytest.raises(PolicyUnavailable):
        await service.mutate(account.id, lambda api: api.idle_collect(), actor=SCHEDULER)

    assert account_env.fake.mutation_count("idle_collect", account.id) == 0


async def test_policy_is_resolved_before_plan_preconditions(account_env):
    account, _ = await account_env.add_token()
    service = AccountService(
        account_env.sessions,
        account_env.service.secret_box,
        account_env.fake,
        token_expiry=account_env.expiries.get,
        clock=account_env.clock,
        sleeper=account_env._sleep,
        jitter=lambda: 0.0,
    )

    with pytest.raises(PolicyUnavailable):
        await service.mutate(
            account.id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            plan_id=uuid4(),
        )

    assert account_env.fake.mutation_count("idle_collect", account.id) == 0


async def test_invalid_plan_uses_stable_error_and_safe_audit_reference(account_env):
    account, _ = await account_env.add_token()
    missing_plan_id = uuid4()

    with pytest.raises(PlanPreconditionFailed):
        await account_env.service.mutate(
            account.id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            plan_id=missing_plan_id,
        )

    async with account_env.sessions() as session:
        audit = await session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.account_id == account.id,
                AuditEvent.action == "account.mutate",
            )
            .order_by(AuditEvent.created_at.desc())
        )
    assert audit is not None
    assert audit.plan_id is None
    assert audit.result == {
        "status": "failed",
        "error": "PlanPreconditionFailed",
    }
    assert account_env.fake.mutation_count("idle_collect", account.id) == 0


async def test_normal_response_is_passed_to_verifier(account_env):
    account, _ = await account_env.add_token()
    seen = []

    async def verified(api, response):
        seen.append(response)
        return True

    outcome = await account_env.service.mutate(
        account.id,
        lambda api: api.idle_collect(),
        actor=SCHEDULER,
        verify=verified,
    )

    assert seen == [outcome.result]
    assert outcome.result is not None
    assert outcome.applied is True
    assert outcome.reconciled is False


async def test_timeout_after_commit_is_reconciled_once_with_none(account_env):
    account, _ = await account_env.add_token()
    account_env.fake.commit_then_timeout("idle_collect", account.id)
    seen = []

    async def idle_counter_increased(api, response):
        seen.append(response)
        return (await api.idle_summary()).accumulated_seconds == 0

    outcome = await account_env.service.mutate(
        account.id,
        lambda api: api.idle_collect(),
        actor=SCHEDULER,
        verify=idle_counter_increased,
    )

    assert outcome == MutationOutcome(applied=True, reconciled=True, result=None)
    assert seen == [None]
    assert account_env.fake.mutation_count("idle_collect", account.id) == 1


@pytest.mark.parametrize("verifier_kind", ["missing", "false", "raises"])
async def test_unresolved_ambiguous_outcome_is_never_repeated(account_env, verifier_kind):
    account, token = await account_env.add_token()
    account_env.fake.commit_then_timeout("idle_collect", account.id)

    async def false_verifier(api, response):
        return False

    async def raising_verifier(api, response):
        raise RuntimeError("verifier-internal-marker")

    verifier = {"missing": None, "false": false_verifier, "raises": raising_verifier}[
        verifier_kind
    ]
    with pytest.raises(ReconciliationRequired) as captured:
        await account_env.service.mutate(
            account.id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            verify=verifier,
        )

    assert account_env.fake.mutation_count("idle_collect", account.id) == 1
    assert token not in str(captured.value)
    assert "verifier-internal-marker" not in str(captured.value)


async def test_mutation_cancellation_is_not_rewritten(account_env):
    account, _ = await account_env.add_token()

    async def cancelled(api):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await account_env.service.mutate(
            account.id,
            cancelled,
            actor=SCHEDULER,
        )


async def test_conflict_refreshes_and_rechecks_then_stops_after_two_retries(account_env):
    account, _ = await account_env.add_token()
    initial_bootstraps = account_env.fake.bootstrap_count(account.id)
    account_env.fake.conflict("idle_collect", account.id, count=3)

    with pytest.raises(GameConflict):
        await account_env.service.mutate(
            account.id, lambda api: api.idle_collect(), actor=SCHEDULER
        )

    assert account_env.fake.mutation_count("idle_collect", account.id) == 3
    assert account_env.fake.bootstrap_count(account.id) >= initial_bootstraps + 4
    assert account_env.policy.requested == [account.id, account.id, account.id]
    assert account_env.delays[-2:] == [0.0, 0.0]


async def test_disabled_and_paused_accounts_block_only_their_own_mutations(account_env):
    disabled, _ = await account_env.add_token("disabled")
    paused, _ = await account_env.add_token("paused")
    healthy, _ = await account_env.add_token("healthy")
    await account_env.service.disable(disabled.id, actor=ADMIN)
    await account_env.service.pause(paused.id, "operator", actor=ADMIN)

    with pytest.raises(AccountDisabled):
        await account_env.service.mutate(
            disabled.id, lambda api: api.idle_collect(), actor=SCHEDULER
        )
    with pytest.raises(AccountPaused):
        await account_env.service.mutate(
            paused.id, lambda api: api.idle_collect(), actor=SCHEDULER
        )

    healthy_snapshot = await account_env.service.snapshot(healthy.id, actor=ADMIN)
    assert healthy_snapshot.enabled is True
    assert account_env.fake.mutation_count("idle_collect") == 0


async def test_public_session_and_mutation_paths_acquire_exactly_one_database_lock(account_env):
    account, _ = await account_env.add_token()
    lock_statements: list[str] = []

    def capture_lock(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            lock_statements.append(statement)

    event.listen(account_env.engine.sync_engine, "before_cursor_execute", capture_lock)
    try:
        await account_env.service.ensure_session(account.id, actor=SCHEDULER)
        assert len(lock_statements) == 1
        lock_statements.clear()
        await account_env.service.mutate(
            account.id, lambda api: api.idle_collect(), actor=SCHEDULER
        )
        assert len(lock_statements) == 1
    finally:
        event.remove(account_env.engine.sync_engine, "before_cursor_execute", capture_lock)


async def test_locked_context_is_scoped_to_one_account(account_env):
    account_a, _ = await account_env.add_token("a")
    account_b, _ = await account_env.add_token("b")

    async with account_env.service.locked(account_a.id) as locked:
        assert locked.account_id == account_a.id
        assert locked.policy.account_id == account_a.id
        assert locked.snapshot.account_id == account_a.id
        assert locked.snapshot.account_id != account_b.id
