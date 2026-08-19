from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.application.idle import (
    IdleExecutionClaims,
    IdleExecutionGuard,
    IdleExecuteUseCase,
    IdlePlanUseCase,
    IdlePreviewStore,
)
from placegame.contracts import Actor
from tests.unit.test_accounts import ServiceEnvironment


pytestmark = pytest.mark.integration

OPERATOR = Actor("webui", "operator")


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
    idle_env.fake.set_idle_seconds(account_a.id, 7200)
    previews = IdlePreviewStore(idle_env.sessions, idle_env.service.repository)
    previewer = IdlePlanUseCase(idle_env.service, previews, clock=idle_env.clock)

    preview = await previewer.preview(
        account_a.id, actor=OPERATOR, correlation_id="preview-alpha"
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


async def test_expired_execution_claim_recovers_without_sending_again(idle_env):
    account, _ = await idle_env.add_token("alpha")
    idle_env.fake.set_idle_seconds(account.id, 7200)
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

    executor = IdleExecuteUseCase(idle_env.service, IdleExecutionGuard(idle_env.sessions), claims)
    with pytest.raises(Exception, match="reconciliation_required"):
        await executor.execute(
            account.id, preview.plan_id, actor=OPERATOR, correlation_id="recover-alpha", recovery=True
        )
    assert idle_env.fake.mutation_count("idle_collect", account.id) == 0
