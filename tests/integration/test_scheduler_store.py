from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from placegame.application.models import IdlePreview
from placegame.contracts import Actor
from placegame.errors import GameUnavailable
from placegame.models import GameAccount, Job, JobRun, SchedulerLease
from placegame.scheduler import (
    PostgresIdlePreviewStore,
    IdlePreviewScheduler,
    SchedulerAccount,
    idle_preview_idempotency_key,
)


pytestmark = pytest.mark.integration


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
INTERVAL_SECONDS = 300
LEASE_SECONDS = 30


@dataclass(frozen=True)
class Account:
    id: UUID
    enabled: bool = True
    paused_reason: str | None = None


class Accounts:
    def __init__(self, accounts: Sequence[Account]) -> None:
        self.accounts = tuple(accounts)

    async def list_accounts(self) -> Sequence[SchedulerAccount]:
        return self.accounts


class Preview:
    def __init__(self, failures: dict[UUID, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[UUID, Actor, str]] = []

    async def preview(
        self, account_id: UUID, *, actor: Actor, correlation_id: str
    ) -> IdlePreview:
        self.calls.append((account_id, actor, correlation_id))
        if failure := self.failures.get(account_id):
            raise failure
        return IdlePreview(
            account_id=account_id,
            plan_id=None,
            decision="wait",
            accumulated_seconds=1,
            capacity_seconds=10,
            threshold_seconds=8,
            expires_at=None,
            reason="idle_threshold_not_reached",
            correlation_id=correlation_id,
        )


@pytest.fixture
def scheduler_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def sessions(scheduler_database_url):
    engine = create_async_engine(scheduler_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE audit_events, job_runs, jobs, action_plans, "
                "account_snapshots, account_policies, game_accounts, scheduler_leases "
                "RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


async def add_accounts(
    factory: async_sessionmaker[AsyncSession],
    count: int,
    *,
    enabled: bool = True,
    paused_reason: str | None = None,
) -> list[Account]:
    created: list[Account] = []
    async with factory.begin() as session:
        for index in range(count):
            record = GameAccount(
                label=f"scheduler-account-{index}-{enabled}-{paused_reason}",
                auth_mode="token_only",
                enabled=enabled,
                paused_reason=paused_reason,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(record)
            created.append(
                Account(id=record.id, enabled=enabled, paused_reason=paused_reason)
            )
    return created


def build_scheduler(
    factory: async_sessionmaker[AsyncSession],
    accounts: Sequence[Account],
    preview: Preview,
    *,
    worker_id: str,
    now: datetime = NOW,
) -> IdlePreviewScheduler:
    return IdlePreviewScheduler(
        factory,
        Accounts(accounts),
        preview,
        store=PostgresIdlePreviewStore(factory),
        clock=lambda: now,
        worker_id=worker_id,
        interval_seconds=INTERVAL_SECONDS,
        lease_seconds=LEASE_SECONDS,
        max_account_concurrency=4,
    )


async def read_jobs(factory: async_sessionmaker[AsyncSession]) -> list[Job]:
    async with factory() as session:
        return list((await session.scalars(select(Job).order_by(Job.created_at))).all())


async def read_runs(factory: async_sessionmaker[AsyncSession]) -> list[JobRun]:
    async with factory() as session:
        return list(
            (await session.scalars(select(JobRun).order_by(JobRun.dispatched_at))).all()
        )


async def read_lease(factory: async_sessionmaker[AsyncSession]) -> SchedulerLease:
    async with factory() as session:
        lease = await session.scalar(
            select(SchedulerLease).where(SchedulerLease.name == "default")
        )
        assert lease is not None
        return lease


def result_of(run: JobRun) -> dict[str, object]:
    assert run.result is not None
    return run.result


async def test_only_one_worker_holds_the_default_lease_at_a_time(sessions):
    store = PostgresIdlePreviewStore(sessions)

    first = await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )
    second = await store.acquire_lease(
        worker_id="worker-b", now=NOW, lease_seconds=LEASE_SECONDS
    )
    reentrant = await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )

    assert (first, second, reentrant) == (True, False, True)

    lease = await read_lease(sessions)
    assert lease.owner == "worker-a"
    assert lease.lease_expires_at == NOW + timedelta(seconds=LEASE_SECONDS)

    await store.release_lease(worker_id="worker-b")
    assert (await read_lease(sessions)).owner == "worker-a"

    await store.release_lease(worker_id="worker-a")
    assert (await read_lease(sessions)).owner is None
    assert await store.acquire_lease(
        worker_id="worker-b", now=NOW, lease_seconds=LEASE_SECONDS
    )


async def test_expired_lease_is_reclaimed_by_another_worker(sessions):
    store = PostgresIdlePreviewStore(sessions)
    assert await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )
    later = NOW + timedelta(seconds=LEASE_SECONDS + 1)

    assert await store.acquire_lease(
        worker_id="worker-b", now=later, lease_seconds=LEASE_SECONDS
    )

    lease = await read_lease(sessions)
    assert lease.owner == "worker-b"


async def test_two_workers_dispatch_only_while_one_holds_the_lease(sessions):
    accounts = await add_accounts(sessions, 2)
    holder_preview = Preview()
    rival_preview = Preview()
    holder = build_scheduler(sessions, accounts, holder_preview, worker_id="worker-a")
    rival = build_scheduler(sessions, accounts, rival_preview, worker_id="worker-b")

    holder_count, rival_count = await asyncio.gather(holder.tick(NOW), rival.tick(NOW))

    assert sorted((holder_count, rival_count)) == [0, 2]
    assert len(holder_preview.calls) + len(rival_preview.calls) == 2
    assert len(await read_runs(sessions)) == 2


async def test_repeated_ticks_create_one_run_per_account_slot(sessions):
    accounts = await add_accounts(sessions, 3)
    preview = Preview()
    subject = build_scheduler(sessions, accounts, preview, worker_id="worker-a")

    assert await subject.tick(NOW) == 3
    assert await subject.tick(NOW) == 0

    runs = await read_runs(sessions)
    assert len(runs) == 3
    assert {run.idempotency_key for run in runs} == {
        idle_preview_idempotency_key(account.id, NOW) for account in accounts
    }
    assert all(run.completed_at == NOW for run in runs)
    assert all(run.lease_owner is None for run in runs)
    assert all(run.attempt == 1 for run in runs)
    assert [call[1] for call in preview.calls] == [Actor("scheduler", "worker-a")] * 3

    jobs = await read_jobs(sessions)
    assert len(jobs) == 3
    assert all(job.kind == "idle_preview" for job in jobs)
    assert all(job.timezone == "Asia/Shanghai" for job in jobs)
    assert all(job.schedule == f"interval:{INTERVAL_SECONDS}" for job in jobs)
    assert all(job.misfire_policy == "defer" for job in jobs)
    assert all(
        job.next_run_at == NOW + timedelta(seconds=INTERVAL_SECONDS) for job in jobs
    )


async def test_next_slot_dispatches_once_the_interval_elapses(sessions):
    accounts = await add_accounts(sessions, 1)
    preview = Preview()
    subject = build_scheduler(sessions, accounts, preview, worker_id="worker-a")
    next_slot = NOW + timedelta(seconds=INTERVAL_SECONDS)

    assert await subject.tick(NOW) == 1
    assert await subject.tick(NOW + timedelta(seconds=INTERVAL_SECONDS - 1)) == 0
    assert await subject.tick(next_slot) == 1

    runs = await read_runs(sessions)
    assert {run.idempotency_key for run in runs} == {
        idle_preview_idempotency_key(accounts[0].id, NOW),
        idle_preview_idempotency_key(accounts[0].id, next_slot),
    }


async def test_overdue_job_defers_to_one_next_slot_instead_of_replaying_backlog(
    sessions,
):
    accounts = await add_accounts(sessions, 1)
    preview = Preview()
    subject = build_scheduler(sessions, accounts, preview, worker_id="worker-a")
    assert await subject.tick(NOW) == 1
    resumed = NOW + timedelta(hours=2)

    assert await subject.tick(resumed) == 1

    assert len(preview.calls) == 2
    jobs = await read_jobs(sessions)
    assert jobs[0].next_run_at == resumed + timedelta(seconds=INTERVAL_SECONDS)


async def test_ineligible_accounts_are_never_provisioned_or_dispatched(sessions):
    active = await add_accounts(sessions, 1)
    disabled = await add_accounts(sessions, 1, enabled=False)
    paused = await add_accounts(sessions, 1, paused_reason="operator")
    removed = await add_accounts(sessions, 1, enabled=False, paused_reason="removed")
    accounts = [*active, *disabled, *paused, *removed]
    preview = Preview()

    assert await build_scheduler(
        sessions, accounts, preview, worker_id="worker-a"
    ).tick(NOW) == 1

    assert [call[0] for call in preview.calls] == [active[0].id]
    jobs = await read_jobs(sessions)
    assert [job.account_id for job in jobs] == [active[0].id]
    assert [run.account_id for run in await read_runs(sessions)] == [active[0].id]


async def test_job_is_disabled_when_its_account_becomes_ineligible(sessions):
    accounts = await add_accounts(sessions, 1)
    preview = Preview()
    assert await build_scheduler(
        sessions, accounts, preview, worker_id="worker-a"
    ).tick(NOW) == 1

    paused = [Account(id=accounts[0].id, enabled=True, paused_reason="operator")]
    later = NOW + timedelta(seconds=INTERVAL_SECONDS)
    assert await build_scheduler(
        sessions, paused, preview, worker_id="worker-a"
    ).tick(later) == 0

    jobs = await read_jobs(sessions)
    assert len(jobs) == 1
    assert jobs[0].enabled is False
    assert len(preview.calls) == 1

    resumed = later + timedelta(seconds=INTERVAL_SECONDS)
    assert await build_scheduler(
        sessions, accounts, preview, worker_id="worker-a"
    ).tick(resumed) == 1
    assert (await read_jobs(sessions))[0].enabled is True


async def test_abandoned_run_is_reclaimed_only_after_its_lease_expires(sessions):
    accounts = await add_accounts(sessions, 1)
    store = PostgresIdlePreviewStore(sessions)
    assert await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )
    await store.ensure_jobs(accounts, now=NOW, interval_seconds=INTERVAL_SECONDS)
    account_ids = frozenset({accounts[0].id})
    claimed = await store.claim_due(
        now=NOW,
        worker_id="worker-a",
        lease_seconds=LEASE_SECONDS,
        account_ids=account_ids,
    )
    assert len(claimed) == 1

    still_leased = await store.claim_due(
        now=NOW + timedelta(seconds=1),
        worker_id="worker-b",
        lease_seconds=LEASE_SECONDS,
        account_ids=account_ids,
    )
    assert still_leased == ()

    expired = NOW + timedelta(seconds=LEASE_SECONDS + 1)
    reclaimed = await store.claim_due(
        now=expired,
        worker_id="worker-b",
        lease_seconds=LEASE_SECONDS,
        account_ids=account_ids,
    )

    assert len(reclaimed) == 1
    assert reclaimed[0].run_id == claimed[0].run_id
    assert reclaimed[0].idempotency_key == claimed[0].idempotency_key
    runs = await read_runs(sessions)
    assert len(runs) == 1
    assert runs[0].attempt == 2
    assert runs[0].lease_owner == "worker-b"


async def test_completed_slot_is_not_redispatched_after_a_restart(sessions):
    accounts = await add_accounts(sessions, 1)
    store = PostgresIdlePreviewStore(sessions)
    assert await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )
    await store.ensure_jobs(accounts, now=NOW, interval_seconds=INTERVAL_SECONDS)
    account_ids = frozenset({accounts[0].id})
    claimed = await store.claim_due(
        now=NOW,
        worker_id="worker-a",
        lease_seconds=LEASE_SECONDS,
        account_ids=account_ids,
    )
    await store.finish_run(
        claimed[0],
        completed_at=NOW,
        next_run_at=NOW + timedelta(seconds=INTERVAL_SECONDS),
        result={"status": "succeeded", "decision": "wait"},
    )

    async with sessions.begin() as session:
        job = await session.scalar(select(Job))
        job.next_run_at = NOW

    stuck = await store.claim_due(
        now=NOW + timedelta(seconds=LEASE_SECONDS + 1),
        worker_id="worker-b",
        lease_seconds=LEASE_SECONDS,
        account_ids=account_ids,
    )

    assert stuck == ()
    assert len(await read_runs(sessions)) == 1


async def test_finish_run_is_idempotent_and_keeps_the_first_result(sessions):
    accounts = await add_accounts(sessions, 1)
    store = PostgresIdlePreviewStore(sessions)
    assert await store.acquire_lease(
        worker_id="worker-a", now=NOW, lease_seconds=LEASE_SECONDS
    )
    await store.ensure_jobs(accounts, now=NOW, interval_seconds=INTERVAL_SECONDS)
    claimed = await store.claim_due(
        now=NOW,
        worker_id="worker-a",
        lease_seconds=LEASE_SECONDS,
        account_ids=frozenset({accounts[0].id}),
    )
    first_slot = NOW + timedelta(seconds=INTERVAL_SECONDS)
    await store.finish_run(
        claimed[0],
        completed_at=NOW,
        next_run_at=first_slot,
        result={"status": "succeeded", "decision": "wait"},
    )

    await store.finish_run(
        claimed[0],
        completed_at=NOW + timedelta(seconds=5),
        next_run_at=NOW + timedelta(hours=1),
        result={"status": "failed", "error": "internal_error"},
    )

    runs = await read_runs(sessions)
    assert runs[0].result == {"status": "succeeded", "decision": "wait"}
    assert runs[0].completed_at == NOW
    assert (await read_jobs(sessions))[0].next_run_at == first_slot


async def test_ten_accounts_stay_isolated_when_one_preview_fails(sessions):
    accounts = await add_accounts(sessions, 10)
    secret_marker = "raw-game-error-must-not-be-persisted"
    failing = accounts[4]
    preview = Preview({failing.id: GameUnavailable(secret_marker)})

    assert await build_scheduler(
        sessions, accounts, preview, worker_id="worker-a"
    ).tick(NOW) == 10

    runs = {run.account_id: run for run in await read_runs(sessions)}
    assert len(runs) == 10
    assert result_of(runs[failing.id])["status"] == "failed"
    assert result_of(runs[failing.id])["error"] == "game_unavailable"
    assert all(
        result_of(runs[account.id])["status"] == "succeeded"
        for account in accounts
        if account.id != failing.id
    )
    assert secret_marker not in str([result_of(run) for run in runs.values()])
    assert all(
        job.next_run_at == NOW + timedelta(seconds=INTERVAL_SECONDS)
        for job in await read_jobs(sessions)
    )
