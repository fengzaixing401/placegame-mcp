import asyncio
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.accounts.repository import AccountRepository
from placegame.application.errors import IdleReconciliationRequired, PlanInProgress
from placegame.application.idle import (
    IdleExecutionClaims,
    IdleExecutionGuard,
    IdleExecuteUseCase,
    IdlePlanUseCase,
    IdlePreviewStore,
)
from placegame.contracts import Actor
from placegame.errors import AccountDisabled, AccountPaused, PlanPreconditionFailed
from placegame.models import ActionPlan
from tests.unit.test_accounts import ServiceEnvironment


pytestmark = pytest.mark.integration

OPERATOR = Actor("webui", "operator")


def _executor(idle_env) -> IdleExecuteUseCase:
    return IdleExecuteUseCase(
        idle_env.service,
        IdleExecutionGuard(idle_env.sessions),
        IdleExecutionClaims(idle_env.sessions, idle_env.service.repository, clock=idle_env.clock),
    )


async def _collect_plan(idle_env, account_id, *, correlation_id: str):
    preview = await asyncio.wait_for(
        IdlePlanUseCase(
            idle_env.service,
            IdlePreviewStore(idle_env.sessions, idle_env.service.repository),
            clock=idle_env.clock,
        ).preview(account_id, actor=OPERATOR, correlation_id=correlation_id),
        timeout=2,
    )
    assert preview.plan_id is not None
    return preview.plan_id


@pytest.fixture
def idle_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def idle_env(idle_database_url, secret_box):
    engine = create_async_engine(idle_database_url)
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


async def test_idle_preview_claim_and_collect_are_account_scoped_and_single_send(idle_env):
    account_a, _ = await idle_env.add_token("alpha")
    account_b, _ = await idle_env.add_token("beta")
    idle_env.fake.set_idle_seconds(account_a.id, 43200)
    previews = IdlePreviewStore(idle_env.sessions, idle_env.service.repository)
    previewer = IdlePlanUseCase(idle_env.service, previews, clock=idle_env.clock)

    preview = await asyncio.wait_for(
        previewer.preview(account_a.id, actor=OPERATOR, correlation_id="preview-alpha"),
        timeout=2,
    )
    assert preview.plan_id is not None
    claims = IdleExecutionClaims(
        idle_env.sessions, idle_env.service.repository, clock=idle_env.clock
    )
    executor = IdleExecuteUseCase(
        idle_env.service, IdleExecutionGuard(idle_env.sessions), claims
    )
    result = await executor.execute(
        account_a.id,
        preview.plan_id,
        actor=OPERATOR,
        correlation_id="execute-alpha",
    )

    assert result.status == "executed"
    assert idle_env.fake.mutation_count("idle_collect", account_a.id) == 1
    assert idle_env.fake.mutation_count("idle_collect", account_b.id) == 0


async def test_pre_send_process_exit_recovery_requires_reconciliation_without_mutation(idle_env):
    account, _ = await idle_env.add_token("alpha")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    previews = IdlePreviewStore(idle_env.sessions, idle_env.service.repository)
    preview = await IdlePlanUseCase(idle_env.service, previews, clock=idle_env.clock).preview(
        account.id, actor=OPERATOR, correlation_id="preview-recovery"
    )
    assert preview.plan_id is not None
    claims = IdleExecutionClaims(idle_env.sessions, idle_env.service.repository, clock=idle_env.clock)
    claim = await claims.claim(
        account.id, preview.plan_id, actor=OPERATOR, correlation_id="claim-recovery", recovery=False
    )
    claim.plan.execution_lease_expires_at = idle_env.clock() - timedelta(seconds=1)
    async with idle_env.sessions.begin() as session:
        row = await session.get(type(claim.plan), claim.plan.id)
        assert row is not None
        row.execution_lease_expires_at = idle_env.clock() - timedelta(seconds=1)

    with pytest.raises(IdleReconciliationRequired):
        await asyncio.wait_for(_executor(idle_env).execute(
            account.id, preview.plan_id, actor=OPERATOR, correlation_id="recover-alpha", recovery=True
        ), timeout=2)
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 0


async def test_ambiguous_collect_reconciles_without_a_second_send(idle_env):
    account, _ = await idle_env.add_token("alpha")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    preview = await IdlePlanUseCase(
        idle_env.service,
        IdlePreviewStore(idle_env.sessions, idle_env.service.repository),
        clock=idle_env.clock,
    ).preview(account.id, actor=OPERATOR, correlation_id="preview-ambiguous")
    assert preview.plan_id is not None
    idle_env.fake.commit_then_timeout("idle_collect", account.id)

    result = await asyncio.wait_for(
        IdleExecuteUseCase(
            idle_env.service,
            IdleExecutionGuard(idle_env.sessions),
            IdleExecutionClaims(idle_env.sessions, idle_env.service.repository, clock=idle_env.clock),
        ).execute(account.id, preview.plan_id, actor=OPERATOR, correlation_id="execute-ambiguous"),
        timeout=2,
    )

    assert result.status == "reconciled"
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 1


async def test_high_risk_plan_is_rejected_without_mutation(idle_env):
    account, _ = await idle_env.add_token("alpha")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    preview = await IdlePlanUseCase(
        idle_env.service,
        IdlePreviewStore(idle_env.sessions, idle_env.service.repository),
        clock=idle_env.clock,
    ).preview(account.id, actor=OPERATOR, correlation_id="preview-high-risk")
    assert preview.plan_id is not None
    async with idle_env.sessions.begin() as session:
        plan = await session.get(ActionPlan, preview.plan_id)
        assert plan is not None
        plan.risk = "high"

    with pytest.raises(PlanPreconditionFailed):
        await IdleExecuteUseCase(
            idle_env.service,
            IdleExecutionGuard(idle_env.sessions),
            IdleExecutionClaims(idle_env.sessions, idle_env.service.repository, clock=idle_env.clock),
        ).execute(account.id, preview.plan_id, actor=OPERATOR, correlation_id="execute-high-risk")
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 0


async def test_duplicate_execute_callers_send_once_and_reject_the_busy_claim(idle_env):
    account, token = await idle_env.add_token("duplicate execute")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    plan_id = await _collect_plan(idle_env, account.id, correlation_id="preview-duplicate")
    barrier = idle_env.fake.bootstrap_barrier(token)
    first = asyncio.create_task(
        _executor(idle_env).execute(account.id, plan_id, actor=OPERATOR, correlation_id="execute-first")
    )
    try:
        await asyncio.wait_for(barrier.started.wait(), timeout=2)
        with pytest.raises(PlanInProgress):
            await asyncio.wait_for(
                _executor(idle_env).execute(
                    account.id, plan_id, actor=OPERATOR, correlation_id="execute-second"
                ),
                timeout=2,
            )
        barrier.release.set()
        result = await asyncio.wait_for(first, timeout=2)
    finally:
        barrier.release.set()
        await asyncio.gather(first, return_exceptions=True)

    assert result.status == "executed"
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 1


@pytest.mark.parametrize(
    ("scenario", "expected_error"),
    [
        ("stale_fingerprint", PlanPreconditionFailed),
        ("expired", PlanPreconditionFailed),
        ("disabled", AccountDisabled),
        ("paused", AccountPaused),
        ("confirmation_required", PlanPreconditionFailed),
        ("extra_decision", PlanPreconditionFailed),
        ("cross_account", PlanPreconditionFailed),
    ],
)
async def test_invalid_idle_plans_do_not_mutate(idle_env, scenario, expected_error):
    account, _ = await idle_env.add_token(f"invalid-{scenario}")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    plan_id = await _collect_plan(idle_env, account.id, correlation_id=f"preview-{scenario}")
    execute_account_id = account.id
    related_accounts = [account.id]

    if scenario == "stale_fingerprint":
        async with idle_env.sessions.begin() as session:
            plan = await session.get(ActionPlan, plan_id)
            assert plan is not None
            plan.state_fingerprint = "stale"
    elif scenario == "expired":
        async with idle_env.sessions.begin() as session:
            plan = await session.get(ActionPlan, plan_id)
            assert plan is not None
            plan.expires_at = idle_env.clock() - timedelta(seconds=1)
    elif scenario == "disabled":
        await idle_env.service.disable(account.id, actor=OPERATOR)
    elif scenario == "paused":
        await idle_env.service.pause(account.id, "operator", actor=OPERATOR)
    elif scenario == "confirmation_required":
        async with idle_env.sessions.begin() as session:
            plan = await session.get(ActionPlan, plan_id)
            assert plan is not None
            plan.confirmation_required = True
    elif scenario == "extra_decision":
        async with idle_env.sessions.begin() as session:
            plan = await session.get(ActionPlan, plan_id)
            assert plan is not None
            plan.proposed_actions = [*plan.proposed_actions, {"family": "idle"}]
    elif scenario == "cross_account":
        other, _ = await idle_env.add_token("invalid-cross-account-target")
        idle_env.fake.set_idle_seconds(other.id, 43200)
        execute_account_id = other.id
        related_accounts.append(other.id)

    with pytest.raises(expected_error):
        await asyncio.wait_for(
            _executor(idle_env).execute(
                execute_account_id,
                plan_id,
                actor=OPERATOR,
                correlation_id=f"execute-{scenario}",
            ),
            timeout=2,
        )
    for related_account_id in related_accounts:
        assert idle_env.fake.mutation_count("idle_collect", related_account_id) == 0


async def test_post_commit_process_exit_recovery_reconciles_without_a_second_send(idle_env):
    account, _ = await idle_env.add_token("post-commit recovery")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    plan_id = await _collect_plan(idle_env, account.id, correlation_id="preview-post-commit")
    claims = IdleExecutionClaims(idle_env.sessions, idle_env.service.repository, clock=idle_env.clock)
    claim = await claims.claim(
        account.id, plan_id, actor=OPERATOR, correlation_id="claim-post-commit", recovery=False
    )
    async with idle_env.service.locked(account.id, actor=OPERATOR) as locked:
        await locked.api.idle_collect()
    async with idle_env.sessions.begin() as session:
        plan = await session.get(ActionPlan, claim.plan.id)
        assert plan is not None
        plan.execution_lease_expires_at = idle_env.clock() - timedelta(seconds=1)

    result = await asyncio.wait_for(
        _executor(idle_env).execute(
            account.id, plan_id, actor=OPERATOR, correlation_id="recover-post-commit", recovery=True
        ),
        timeout=2,
    )

    assert result.status == "reconciled"
    assert result.applied is False
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 1


async def test_idle_preview_rolls_back_the_plan_when_audit_write_fails(idle_env, monkeypatch):
    account, _ = await idle_env.add_token("preview audit rollback")
    idle_env.fake.set_idle_seconds(account.id, 43200)
    preview_repository = AccountRepository()

    async def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(preview_repository, "add_audit", reject_audit)
    previewer = IdlePlanUseCase(
        idle_env.service,
        IdlePreviewStore(idle_env.sessions, preview_repository),
        clock=idle_env.clock,
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        await asyncio.wait_for(
            previewer.preview(account.id, actor=OPERATOR, correlation_id="preview-audit-rollback"),
            timeout=2,
        )
    async with idle_env.sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(ActionPlan).where(ActionPlan.account_id == account.id)
        )
    assert count == 0
