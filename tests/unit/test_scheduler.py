from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from placegame.application.errors import ApplicationError
from placegame.application.models import IdlePreview
from placegame.config import Settings
from placegame.contracts import Actor
from placegame.errors import GameUnavailable
from placegame.scheduler import (
    ClaimedIdlePreviewRun,
    IdlePreviewScheduler,
    SchedulerAccount,
    idle_preview_idempotency_key,
)


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
WORKER_ID = "scheduler-worker"


@dataclass(frozen=True)
class FakeAccount:
    id: UUID
    enabled: bool = True
    paused_reason: str | None = None


@dataclass(frozen=True)
class FinishedRun:
    claimed: ClaimedIdlePreviewRun
    completed_at: datetime
    next_run_at: datetime
    result: dict[str, object]


class FakeAccounts:
    def __init__(self, accounts: Sequence[FakeAccount]) -> None:
        self.accounts = tuple(accounts)
        self.list_calls = 0

    async def list_accounts(self) -> Sequence[SchedulerAccount]:
        self.list_calls += 1
        return self.accounts


class FakeStore:
    def __init__(
        self,
        claims: Sequence[ClaimedIdlePreviewRun],
        *,
        lease_holder: str = WORKER_ID,
    ) -> None:
        self.claims = tuple(claims)
        self.lease_holder = lease_holder
        self.lease_attempts: list[str] = []
        self.ensured_accounts: list[tuple[SchedulerAccount, ...]] = []
        self.claim_requests: list[frozenset[UUID]] = []
        self.finished: list[FinishedRun] = []
        self.released: list[str] = []
        self.lease_attempted = asyncio.Event()
        self._claimed_keys: set[str] = set()

    async def acquire_lease(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool:
        del now, lease_seconds
        self.lease_attempts.append(worker_id)
        self.lease_attempted.set()
        return worker_id == self.lease_holder

    async def ensure_jobs(
        self,
        accounts: Sequence[SchedulerAccount],
        *,
        now: datetime,
        interval_seconds: int,
    ) -> None:
        del now, interval_seconds
        self.ensured_accounts.append(tuple(accounts))

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
        account_ids: frozenset[UUID],
    ) -> tuple[ClaimedIdlePreviewRun, ...]:
        del worker_id, lease_seconds
        self.claim_requests.append(account_ids)
        claimed: list[ClaimedIdlePreviewRun] = []
        for run in self.claims:
            if (
                run.account_id in account_ids
                and run.scheduled_for <= now
                and run.idempotency_key not in self._claimed_keys
            ):
                self._claimed_keys.add(run.idempotency_key)
                claimed.append(run)
        return tuple(claimed)

    async def finish_run(
        self,
        claimed: ClaimedIdlePreviewRun,
        *,
        completed_at: datetime,
        next_run_at: datetime,
        result: dict[str, object],
    ) -> None:
        self.finished.append(
            FinishedRun(
                claimed=claimed,
                completed_at=completed_at,
                next_run_at=next_run_at,
                result=result,
            )
        )

    async def release_lease(self, *, worker_id: str) -> None:
        self.released.append(worker_id)


class FinishFailingStore(FakeStore):
    def __init__(
        self, claims: Sequence[ClaimedIdlePreviewRun], *, failing_account: UUID
    ) -> None:
        super().__init__(claims)
        self.failing_account = failing_account

    async def finish_run(
        self,
        claimed: ClaimedIdlePreviewRun,
        *,
        completed_at: datetime,
        next_run_at: datetime,
        result: dict[str, object],
    ) -> None:
        if claimed.account_id == self.failing_account:
            raise RuntimeError("primary is down")
        await super().finish_run(
            claimed,
            completed_at=completed_at,
            next_run_at=next_run_at,
            result=result,
        )


class PreviewFake:
    def __init__(self, failures: dict[UUID, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[UUID, Actor, str]] = []
        self.idle_collect_calls = 0

    async def preview(
        self, account_id: UUID, *, actor: Actor, correlation_id: str
    ) -> IdlePreview:
        self.calls.append((account_id, actor, correlation_id))
        if failure := self.failures.get(account_id):
            raise failure
        return preview_for(account_id, correlation_id)

    async def idle_collect(self) -> None:
        self.idle_collect_calls += 1


class BlockingPreviewFake(PreviewFake):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.four_started = asyncio.Event()
        self.release = asyncio.Event()

    async def preview(
        self, account_id: UUID, *, actor: Actor, correlation_id: str
    ) -> IdlePreview:
        self.calls.append((account_id, actor, correlation_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= 4:
            self.four_started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return preview_for(account_id, correlation_id)


def preview_for(account_id: UUID, correlation_id: str) -> IdlePreview:
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


def claimed_run(account_id: UUID, *, scheduled_for: datetime = NOW) -> ClaimedIdlePreviewRun:
    return ClaimedIdlePreviewRun(
        run_id=uuid4(),
        job_id=uuid4(),
        account_id=account_id,
        scheduled_for=scheduled_for,
        idempotency_key=idle_preview_idempotency_key(account_id, scheduled_for),
    )


def scheduler(
    accounts: Sequence[FakeAccount],
    store: FakeStore,
    preview: PreviewFake,
    *,
    worker_id: str = WORKER_ID,
    max_account_concurrency: int = 4,
    clock: Callable[[], datetime] | None = None,
) -> IdlePreviewScheduler:
    return IdlePreviewScheduler(
        cast(async_sessionmaker[AsyncSession], object()),
        FakeAccounts(accounts),
        preview,
        store=store,
        clock=clock or (lambda: NOW),
        worker_id=worker_id,
        interval_seconds=300,
        lease_seconds=30,
        max_account_concurrency=max_account_concurrency,
    )


def test_scheduler_settings_have_a_five_minute_interval_and_process_local_worker_id() -> None:
    first = Settings.model_validate({})
    second = Settings.model_validate({})

    assert first.scheduler_interval_seconds == 300
    assert first.max_account_concurrency == 4
    assert re.fullmatch(r"[0-9a-f]{32}", first.scheduler_worker_id)
    assert first.scheduler_worker_id == second.scheduler_worker_id
    configured = Settings.model_validate({"scheduler_worker_id": "configured-worker"})
    assert configured.scheduler_worker_id == "configured-worker"


async def test_only_the_lease_holder_dispatches_preview_runs() -> None:
    account = FakeAccount(uuid4())
    store = FakeStore([claimed_run(account.id)], lease_holder="lease-holder")
    preview = PreviewFake()
    holder = scheduler([account], store, preview, worker_id="lease-holder")
    non_holder = scheduler([account], store, preview, worker_id="other-worker")

    assert await non_holder.tick(NOW) == 0
    assert await holder.tick(NOW) == 1

    assert store.lease_attempts == ["other-worker", "lease-holder"]
    assert [call[0] for call in preview.calls] == [account.id]
    assert store.released == ["lease-holder"]


async def test_each_account_slot_is_claimed_once_with_a_deterministic_key() -> None:
    accounts = [FakeAccount(uuid4()), FakeAccount(uuid4())]
    store = FakeStore([claimed_run(account.id) for account in accounts])
    preview = PreviewFake()
    subject = scheduler(accounts, store, preview)

    assert await subject.tick(NOW) == 2
    assert await subject.tick(NOW) == 0

    assert [call[0] for call in preview.calls] == [account.id for account in accounts]
    assert {run.claimed.idempotency_key for run in store.finished} == {
        idle_preview_idempotency_key(account.id, NOW) for account in accounts
    }
    assert all(
        run.next_run_at == NOW + timedelta(minutes=5) for run in store.finished
    )


async def test_overdue_slot_defers_to_the_next_interval_without_replaying_backlog() -> None:
    account = FakeAccount(uuid4())
    overdue = NOW - timedelta(hours=1)
    store = FakeStore([claimed_run(account.id, scheduled_for=overdue)])
    preview = PreviewFake()

    assert await scheduler([account], store, preview).tick(NOW) == 1

    assert store.finished[0].next_run_at == NOW + timedelta(minutes=5)


async def test_disabled_paused_and_removed_accounts_are_not_previewed() -> None:
    active = FakeAccount(uuid4())
    disabled = FakeAccount(uuid4(), enabled=False)
    paused = FakeAccount(uuid4(), paused_reason="operator")
    removed = FakeAccount(uuid4(), enabled=False, paused_reason="removed")
    accounts = [active, disabled, paused, removed]
    store = FakeStore([claimed_run(account.id) for account in accounts])
    preview = PreviewFake()

    assert await scheduler(accounts, store, preview).tick(NOW) == 1

    assert store.ensured_accounts == [tuple(accounts)]
    assert store.claim_requests == [frozenset({active.id})]
    assert [call[0] for call in preview.calls] == [active.id]
    assert preview.idle_collect_calls == 0


async def test_preview_delegation_uses_the_scheduler_actor_and_fresh_safe_correlation() -> None:
    account = FakeAccount(uuid4())
    store = FakeStore([claimed_run(account.id)])
    preview = PreviewFake()

    assert await scheduler([account], store, preview).tick(NOW) == 1

    called_account, actor, correlation_id = preview.calls[0]
    assert called_account == account.id
    assert actor == Actor("scheduler", WORKER_ID)
    assert re.fullmatch(r"[0-9a-f]{32}", correlation_id)
    assert store.finished[0].result == {
        "status": "succeeded",
        "decision": "wait",
        "plan_id": None,
        "correlation_id": correlation_id,
    }
    assert preview.idle_collect_calls == 0


async def test_preview_failures_are_isolated_and_results_do_not_retain_error_text() -> None:
    healthy = FakeAccount(uuid4())
    unavailable = FakeAccount(uuid4())
    unexpected = FakeAccount(uuid4())
    secret_marker = "raw-game-error-must-not-be-persisted"
    preview = PreviewFake(
        {
            unavailable.id: GameUnavailable(secret_marker),
            unexpected.id: RuntimeError(secret_marker),
        }
    )
    store = FakeStore(
        [claimed_run(account.id) for account in (healthy, unavailable, unexpected)]
    )

    assert await scheduler([healthy, unavailable, unexpected], store, preview).tick(NOW) == 3

    results = {run.claimed.account_id: run.result for run in store.finished}
    assert results[healthy.id]["status"] == "succeeded"
    assert results[unavailable.id]["status"] == "failed"
    assert results[unavailable.id]["error"] == "game_unavailable"
    assert isinstance(results[unavailable.id]["correlation_id"], str)
    assert results[unexpected.id]["status"] == "failed"
    assert results[unexpected.id]["error"] == "internal_error"
    assert isinstance(results[unexpected.id]["correlation_id"], str)
    assert secret_marker not in str(results)
    assert preview.idle_collect_calls == 0


async def test_application_errors_use_their_stable_code() -> None:
    account = FakeAccount(uuid4())
    preview = PreviewFake({account.id: ApplicationError("scheduler_policy_unavailable")})
    store = FakeStore([claimed_run(account.id)])

    assert await scheduler([account], store, preview).tick(NOW) == 1

    assert store.finished[0].result["error"] == "scheduler_policy_unavailable"


async def test_preview_concurrency_is_limited_to_four_accounts() -> None:
    accounts = [FakeAccount(uuid4()) for _ in range(10)]
    store = FakeStore([claimed_run(account.id) for account in accounts])
    preview = BlockingPreviewFake()
    task = asyncio.create_task(scheduler(accounts, store, preview).tick(NOW))

    await asyncio.wait_for(preview.four_started.wait(), timeout=1)
    assert preview.max_active == 4
    assert len(preview.calls) == 4
    preview.release.set()

    assert await task == 10
    assert preview.max_active == 4
    assert preview.idle_collect_calls == 0


async def test_run_does_not_dispatch_when_already_stopped() -> None:
    account = FakeAccount(uuid4())
    store = FakeStore([claimed_run(account.id)])
    preview = PreviewFake()
    subject = scheduler([account], store, preview)
    stop = asyncio.Event()
    stop.set()

    await subject.run(stop)

    assert preview.calls == []
    assert store.lease_attempts == []


async def test_close_stops_a_running_loop_promptly_and_is_idempotent() -> None:
    store = FakeStore([])
    subject = scheduler([], store, PreviewFake())
    task = asyncio.create_task(subject.run(asyncio.Event()))
    await asyncio.wait_for(store.lease_attempted.wait(), timeout=1)

    await subject.close()
    await subject.close()

    await asyncio.wait_for(task, timeout=0.2)
    assert await subject.tick(NOW) == 0


async def test_completed_at_is_read_at_finish_while_the_next_slot_stays_anchored() -> None:
    account = FakeAccount(uuid4())
    store = FakeStore([claimed_run(account.id)])
    readings = iter([NOW, NOW + timedelta(seconds=7)])
    subject = scheduler(
        [account], store, PreviewFake(), clock=lambda: next(readings)
    )

    assert await subject.tick() == 1

    finished = store.finished[0]
    assert finished.completed_at == NOW + timedelta(seconds=7)
    assert finished.next_run_at == NOW + timedelta(seconds=300)


async def test_a_storage_failure_is_isolated_and_logged_without_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    accounts = [FakeAccount(uuid4()) for _ in range(3)]
    failing = accounts[1]
    store = FinishFailingStore(
        [claimed_run(account.id) for account in accounts],
        failing_account=failing.id,
    )
    preview = PreviewFake()

    with caplog.at_level(logging.ERROR, logger="placegame.scheduler"):
        assert await scheduler(accounts, store, preview).tick(NOW) == 3

    assert len(preview.calls) == 3
    assert {run.claimed.account_id for run in store.finished} == {
        account.id for account in accounts if account.id != failing.id
    }
    assert store.released == [WORKER_ID]
    events = [
        record.getMessage()
        for record in caplog.records
        if record.name == "placegame.scheduler"
    ]
    assert events == ["scheduler_run_not_recorded"]
    assert "primary is down" not in caplog.text
