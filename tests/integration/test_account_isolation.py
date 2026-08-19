import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.accounts.locks import account_lock
from placegame.errors import AccountPaused, AccountRemoved, ReconciliationRequired
from placegame.models import (
    ActionPlan,
    AccountSnapshot as AccountSnapshotRecord,
    GameAccount,
    Job,
)
from tests.unit.test_accounts import ADMIN, SCHEDULER, ServiceEnvironment


pytestmark = pytest.mark.integration


@pytest.fixture
def isolation_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def isolation_env(isolation_database_url, secret_box):
    engine = create_async_engine(isolation_database_url)
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


async def test_postgres_advisory_locks_serialize_one_account_but_not_another(isolation_env):
    account_a, _ = await isolation_env.add_token("a")
    account_b, _ = await isolation_env.add_token("b")
    a_release = asyncio.Event()
    a_acquired = asyncio.Event()
    same_acquired = asyncio.Event()
    other_acquired = asyncio.Event()

    async def hold(account_id, acquired, release):
        async with isolation_env.sessions() as session:
            async with session.begin():
                async with account_lock(session, account_id):
                    acquired.set()
                    await release.wait()

    holder = asyncio.create_task(hold(account_a.id, a_acquired, a_release))
    await asyncio.wait_for(a_acquired.wait(), timeout=2)
    same = asyncio.create_task(hold(account_a.id, same_acquired, asyncio.Event()))
    other = asyncio.create_task(hold(account_b.id, other_acquired, asyncio.Event()))
    await asyncio.wait_for(other_acquired.wait(), timeout=2)
    await asyncio.sleep(0.05)
    assert same_acquired.is_set() is False
    assert other_acquired.is_set() is True

    a_release.set()
    await asyncio.wait_for(holder, timeout=2)
    same.cancel()
    other.cancel()
    await asyncio.gather(same, other, return_exceptions=True)


async def test_service_mutations_serialize_one_account_but_not_another(isolation_env):
    account_a, _ = await isolation_env.add_token("a")
    account_b, _ = await isolation_env.add_token("b")
    first_started = asyncio.Event()
    same_started = asyncio.Event()
    other_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation(api):
        first_started.set()
        await release_first.wait()
        return await api.idle_collect()

    async def same_operation(api):
        same_started.set()
        return await api.idle_collect()

    async def other_operation(api):
        other_started.set()
        return await api.idle_collect()

    first = asyncio.create_task(
        isolation_env.service.mutate(
            account_a.id, first_operation, actor=SCHEDULER
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=2)
    same = asyncio.create_task(
        isolation_env.service.mutate(
            account_a.id, same_operation, actor=SCHEDULER
        )
    )
    other = asyncio.create_task(
        isolation_env.service.mutate(
            account_b.id, other_operation, actor=SCHEDULER
        )
    )
    try:
        await asyncio.wait_for(other_started.wait(), timeout=2)
        await asyncio.wait_for(other, timeout=2)
        assert same_started.is_set() is False
    finally:
        release_first.set()
        await asyncio.wait_for(first, timeout=2)
        await asyncio.wait_for(same, timeout=2)


async def test_ten_accounts_keep_credentials_snapshots_plans_jobs_and_policy_isolated(
    isolation_env,
):
    accounts = []
    for index in range(10):
        if index in {0, 5}:
            account, _, _, _ = await isolation_env.add_credentials(f"account-{index}")
        else:
            account, _ = await isolation_env.add_token(f"account-{index}")
        accounts.append(account)

    snapshots = {
        account.id: await isolation_env.service.snapshot(account.id, actor=ADMIN)
        for account in accounts
    }
    plans = {}
    async with isolation_env.sessions.begin() as session:
        for account in accounts:
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
            plan = ActionPlan(
                account_id=account.id,
                state_fingerprint=snapshots[account.id].state_fingerprint,
                policy_version=1,
                proposed_actions=[{"kind": "idle_collect"}],
                estimated_costs={},
                risk="low",
                expires_at=isolation_env.clock() + timedelta(minutes=5),
                confirmation_required=False,
            )
            session.add(plan)
            plans[account.id] = plan
        await session.flush()

    async with isolation_env.sessions() as session:
        before_accounts = {
            row.id: (
                row.enabled,
                row.paused_reason,
                row._game_username,
                row._password,
                row._session_token,
            )
            for row in (await session.scalars(select(GameAccount))).all()
            if row.id != accounts[0].id
        }
        before_jobs = {
            row.account_id: (row.enabled, row.next_run_at)
            for row in (await session.scalars(select(Job))).all()
            if row.account_id != accounts[0].id
        }
        before_plans = {
            row.account_id: (row.execution_state, row.execution_result)
            for row in (await session.scalars(select(ActionPlan))).all()
            if row.account_id != accounts[0].id
        }
        before_snapshots = {
            row.account_id: (row.state_fingerprint, row.fetched_at, row.expires_at)
            for row in (await session.scalars(select(AccountSnapshotRecord))).all()
            if row.account_id != accounts[0].id
        }

    async with isolation_env.sessions.begin() as session:
        account_zero = await session.get(GameAccount, accounts[0].id)
        account_zero.session_expires_at = isolation_env.clock() + timedelta(hours=23)
    await isolation_env.service.ensure_session(accounts[0].id, actor=SCHEDULER)
    await isolation_env.service.pause(accounts[0].id, "operator", actor=ADMIN)
    await isolation_env.service.resume(accounts[0].id, actor=ADMIN)
    isolation_env.fake.commit_then_timeout("idle_collect", accounts[0].id)

    async def plan_fingerprint(api):
        return snapshots[accounts[0].id].state_fingerprint

    with pytest.raises(ReconciliationRequired):
        await isolation_env.service.mutate(
            accounts[0].id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            plan_id=plans[accounts[0].id].id,
            state_fingerprint=plan_fingerprint,
        )
    await isolation_env.service.disable(accounts[0].id, actor=ADMIN)

    async with isolation_env.sessions() as session:
        after_accounts = {
            row.id: (
                row.enabled,
                row.paused_reason,
                row._game_username,
                row._password,
                row._session_token,
            )
            for row in (await session.scalars(select(GameAccount))).all()
            if row.id != accounts[0].id
        }
        after_jobs = {
            row.account_id: (row.enabled, row.next_run_at)
            for row in (await session.scalars(select(Job))).all()
            if row.account_id != accounts[0].id
        }
        after_plans = {
            row.account_id: (row.execution_state, row.execution_result)
            for row in (await session.scalars(select(ActionPlan))).all()
            if row.account_id != accounts[0].id
        }
        after_snapshots = {
            row.account_id: (row.state_fingerprint, row.fetched_at, row.expires_at)
            for row in (await session.scalars(select(AccountSnapshotRecord))).all()
            if row.account_id != accounts[0].id
        }

    assert after_accounts == before_accounts
    assert after_jobs == before_jobs
    assert after_plans == before_plans
    assert after_snapshots == before_snapshots
    assert (await isolation_env.policy.get(accounts[0].id)).version == 1
    assert isolation_env.fake.mutation_count("idle_collect", accounts[0].id) == 1
    assert all(
        isolation_env.fake.mutation_count("idle_collect", account.id) == 0
        for account in accounts[1:]
    )


async def test_disable_drain_removes_only_after_lock_drains(isolation_env):
    account, _ = await isolation_env.add_token("drain")
    async with isolation_env.sessions() as held_session:
        async with held_session.begin():
            async with account_lock(held_session, account.id):
                removal = asyncio.create_task(
                    isolation_env.service.disable_drain_remove(account.id, actor=ADMIN)
                )
                current: GameAccount | None = None
                for _ in range(50):
                    async with isolation_env.sessions() as observer:
                        current = await observer.get(GameAccount, account.id)
                    if current is not None and current.paused_reason == "removing":
                        break
                    await asyncio.sleep(0.01)
                assert current is not None
                assert current.enabled is False
                assert current.paused_reason == "removing"
                assert removal.done() is False
                enable = asyncio.create_task(
                    isolation_env.service.enable(account.id, actor=ADMIN)
                )
                mutation = asyncio.create_task(
                    isolation_env.service.mutate(
                        account.id,
                        lambda api: api.idle_collect(),
                        actor=SCHEDULER,
                    )
                )
                await asyncio.sleep(0.05)
                assert enable.done() is False
                assert mutation.done() is False
    receipt = await asyncio.wait_for(removal, timeout=2)
    with pytest.raises((AccountPaused, AccountRemoved)):
        await asyncio.wait_for(enable, timeout=2)
    with pytest.raises((AccountPaused, AccountRemoved)):
        await asyncio.wait_for(mutation, timeout=2)

    assert receipt.account_id == account.id
    assert (await isolation_env.service.get(account.id)).paused_reason == "removed"


async def test_credential_edit_owns_row_before_removal_marker(isolation_env):
    account, _, password, current_token = await isolation_env.add_credentials(
        "credential edit"
    )
    barrier = isolation_env.fake.bootstrap_barrier(current_token)
    holder_acquired = asyncio.Event()
    holder_release = asyncio.Event()

    async def hold_account_lock_until_released():
        async with isolation_env.sessions() as session:
            async with session.begin():
                async with account_lock(session, account.id):
                    holder_acquired.set()
                    await holder_release.wait()

    async def wait_until_paused_reason(target: str):
        for _ in range(50):
            if (await isolation_env.service.get(account.id)).paused_reason == target:
                return
            await asyncio.sleep(0.01)
        pytest.fail(f"paused reason did not become {target}")

    edit = asyncio.create_task(
        isolation_env.service.update_credentials(
            account.id, None, password, actor=ADMIN
        )
    )
    holder = None
    removal = None
    try:
        await asyncio.wait_for(barrier.started.wait(), timeout=2)
        holder = asyncio.create_task(hold_account_lock_until_released())
        removal = asyncio.create_task(
            isolation_env.service.disable_drain_remove(account.id, actor=ADMIN)
        )
        await asyncio.sleep(0.05)

        observed = await isolation_env.service.get(account.id)
        assert observed.paused_reason != "removing"

        barrier.release.set()
        await asyncio.wait_for(edit, timeout=2)
        await asyncio.wait_for(holder_acquired.wait(), timeout=2)
        await wait_until_paused_reason("removing")
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal
        assert (
            await isolation_env.service.get(account.id)
        ).paused_reason == "removing"
        holder_release.set()
    finally:
        barrier.release.set()
        holder_release.set()
        tasks = [task for task in (edit, holder, removal) if task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_token_edit_owns_row_before_removal_marker(isolation_env):
    account, current_token = await isolation_env.add_token("token edit")
    alias_token = f"alias-{uuid4().hex}"
    isolation_env.fake.alias_token(alias_token, current_token)
    barrier = isolation_env.fake.bootstrap_barrier(alias_token)
    holder_acquired = asyncio.Event()
    holder_release = asyncio.Event()

    async def hold_account_lock_until_released():
        async with isolation_env.sessions() as session:
            async with session.begin():
                async with account_lock(session, account.id):
                    holder_acquired.set()
                    await holder_release.wait()

    async def wait_until_paused_reason(target: str):
        for _ in range(50):
            if (await isolation_env.service.get(account.id)).paused_reason == target:
                return
            await asyncio.sleep(0.01)
        pytest.fail(f"paused reason did not become {target}")

    edit = asyncio.create_task(
        isolation_env.service.update_token_only(
            account.id, alias_token, actor=ADMIN
        )
    )
    holder = None
    removal = None
    try:
        await asyncio.wait_for(barrier.started.wait(), timeout=2)
        holder = asyncio.create_task(hold_account_lock_until_released())
        removal = asyncio.create_task(
            isolation_env.service.disable_drain_remove(account.id, actor=ADMIN)
        )
        await asyncio.sleep(0.05)

        observed = await isolation_env.service.get(account.id)
        assert observed.paused_reason != "removing"

        barrier.release.set()
        await asyncio.wait_for(edit, timeout=2)
        await asyncio.wait_for(holder_acquired.wait(), timeout=2)
        await wait_until_paused_reason("removing")
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal
        assert (
            await isolation_env.service.get(account.id)
        ).paused_reason == "removing"
        holder_release.set()
    finally:
        barrier.release.set()
        holder_release.set()
        tasks = [task for task in (edit, holder, removal) if task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)
