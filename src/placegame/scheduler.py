from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from placegame.application.errors import ApplicationError
from placegame.application.models import IdlePreview
from placegame.contracts import Actor
from placegame.errors import (
    AccountDisabled,
    AccountIdentityConflict,
    AccountNotFound,
    AccountPaused,
    AccountRemoved,
    AmbiguousMutation,
    AuthenticationRequired,
    ContractChanged,
    GameConflict,
    GameHttpError,
    GameRateLimited,
    GameSchemaMismatch,
    GameUnavailable,
    InsufficientResource,
    InventoryFull,
    PlanPreconditionFailed,
    PolicyUnavailable,
    ReconciliationRequired,
    SessionRejected,
)
from placegame.models import Job, JobRun, SchedulerLease


Clock = Callable[[], datetime]

LEASE_NAME = "default"
JOB_KIND = "idle_preview"
JOB_TIMEZONE = "Asia/Shanghai"
JOB_MISFIRE_POLICY = "defer"


class SchedulerAccount(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def paused_reason(self) -> str | None: ...


@dataclass(frozen=True)
class ClaimedIdlePreviewRun:
    run_id: UUID
    job_id: UUID
    account_id: UUID
    scheduled_for: datetime
    idempotency_key: str


class IdlePreviewSchedulerStore(Protocol):
    async def acquire_lease(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool: ...

    async def ensure_jobs(
        self,
        accounts: Sequence[SchedulerAccount],
        *,
        now: datetime,
        interval_seconds: int,
    ) -> None: ...

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
        account_ids: frozenset[UUID],
    ) -> tuple[ClaimedIdlePreviewRun, ...]: ...

    async def finish_run(
        self,
        claimed: ClaimedIdlePreviewRun,
        *,
        completed_at: datetime,
        next_run_at: datetime,
        result: dict[str, object],
    ) -> None: ...

    async def release_lease(self, *, worker_id: str) -> None: ...


class AccountServicePort(Protocol):
    async def list_accounts(self) -> Sequence[SchedulerAccount]: ...


class IdlePreviewUseCasePort(Protocol):
    async def preview(
        self, account_id: UUID, *, actor: Actor, correlation_id: str
    ) -> IdlePreview: ...


def idle_preview_idempotency_key(account_id: UUID, scheduled_for: datetime) -> str:
    """Return a stable, bounded key for one account and scheduled slot."""

    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    slot = scheduled_for.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return f"idle_preview:{account_id}:{slot}"


class PostgresIdlePreviewStore:
    """Elects one worker per tick and records each account slot exactly once."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def acquire_lease(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool:
        async with self.sessions.begin() as session:
            await session.execute(
                pg_insert(SchedulerLease)
                .values(name=LEASE_NAME, updated_at=now)
                .on_conflict_do_nothing(index_elements=["name"])
            )
            lease = await session.scalar(
                select(SchedulerLease)
                .where(SchedulerLease.name == LEASE_NAME)
                .with_for_update()
            )
            if lease is None:
                return False
            if (
                lease.owner is not None
                and lease.owner != worker_id
                and lease.lease_expires_at is not None
                and lease.lease_expires_at > now
            ):
                return False
            lease.owner = worker_id
            lease.lease_expires_at = now + timedelta(seconds=lease_seconds)
            lease.updated_at = now
            await session.flush()
            return True

    async def ensure_jobs(
        self,
        accounts: Sequence[SchedulerAccount],
        *,
        now: datetime,
        interval_seconds: int,
    ) -> None:
        known = frozenset(account.id for account in accounts)
        if not known:
            return
        eligible = frozenset(
            account.id
            for account in accounts
            if account.enabled and account.paused_reason is None
        )
        schedule = f"interval:{interval_seconds}"
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(Job)
                    .where(Job.kind == JOB_KIND, Job.account_id.in_(known))
                    .order_by(Job.account_id, Job.created_at)
                    .with_for_update()
                )
            ).all()
            current: dict[UUID, Job] = {}
            for row in rows:
                if row.account_id in current:
                    # At most one idle-preview job per account stays enabled.
                    row.enabled = False
                else:
                    current[row.account_id] = row
            for account_id in known:
                job = current.get(account_id)
                if account_id not in eligible:
                    if job is not None:
                        job.enabled = False
                    continue
                if job is None:
                    session.add(
                        Job(
                            account_id=account_id,
                            kind=JOB_KIND,
                            schedule=schedule,
                            timezone=JOB_TIMEZONE,
                            enabled=True,
                            next_run_at=now,
                            misfire_policy=JOB_MISFIRE_POLICY,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    continue
                job.schedule = schedule
                job.timezone = JOB_TIMEZONE
                job.misfire_policy = JOB_MISFIRE_POLICY
                job.enabled = True
                if job.next_run_at is None:
                    job.next_run_at = now
            await session.flush()

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
        account_ids: frozenset[UUID],
    ) -> tuple[ClaimedIdlePreviewRun, ...]:
        if not account_ids:
            return ()
        claimed: list[ClaimedIdlePreviewRun] = []
        async with self.sessions.begin() as session:
            due = (
                await session.scalars(
                    select(Job)
                    .where(
                        Job.kind == JOB_KIND,
                        Job.enabled.is_(True),
                        Job.account_id.in_(account_ids),
                        Job.next_run_at.is_not(None),
                        Job.next_run_at <= now,
                    )
                    .order_by(Job.account_id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for job in due:
                scheduled_for = job.next_run_at
                if scheduled_for is None:
                    continue
                run = await self._claim_slot(
                    session,
                    job,
                    scheduled_for,
                    now=now,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
                if run is not None:
                    claimed.append(run)
        return tuple(claimed)

    async def _claim_slot(
        self,
        session: AsyncSession,
        job: Job,
        scheduled_for: datetime,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
    ) -> ClaimedIdlePreviewRun | None:
        idempotency_key = idle_preview_idempotency_key(job.account_id, scheduled_for)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        run = JobRun(
            job_id=job.id,
            account_id=job.account_id,
            idempotency_key=idempotency_key,
            dispatched_at=now,
            lease_owner=worker_id,
            lease_expires_at=lease_expires_at,
            attempt=1,
        )
        try:
            async with session.begin_nested():
                session.add(run)
                await session.flush()
        except IntegrityError:
            # The slot already exists; only an abandoned, unfinished run is retaken.
            existing = await session.scalar(
                select(JobRun)
                .where(
                    JobRun.account_id == job.account_id,
                    JobRun.idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
            if existing is None or existing.completed_at is not None:
                return None
            if (
                existing.lease_expires_at is not None
                and existing.lease_expires_at > now
            ):
                return None
            existing.lease_owner = worker_id
            existing.lease_expires_at = lease_expires_at
            existing.attempt += 1
            await session.flush()
            run = existing
        return ClaimedIdlePreviewRun(
            run_id=run.id,
            job_id=job.id,
            account_id=job.account_id,
            scheduled_for=scheduled_for,
            idempotency_key=idempotency_key,
        )

    async def finish_run(
        self,
        claimed: ClaimedIdlePreviewRun,
        *,
        completed_at: datetime,
        next_run_at: datetime,
        result: dict[str, object],
    ) -> None:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(JobRun).where(JobRun.id == claimed.run_id).with_for_update()
            )
            if run is None or run.completed_at is not None:
                return
            run.result = dict(result)
            run.completed_at = completed_at
            run.lease_owner = None
            run.lease_expires_at = None
            job = await session.scalar(
                select(Job).where(Job.id == claimed.job_id).with_for_update()
            )
            if job is not None:
                job.next_run_at = next_run_at
            await session.flush()

    async def release_lease(self, *, worker_id: str) -> None:
        async with self.sessions.begin() as session:
            lease = await session.scalar(
                select(SchedulerLease)
                .where(SchedulerLease.name == LEASE_NAME)
                .with_for_update()
            )
            if lease is None or lease.owner != worker_id:
                return
            lease.owner = None
            lease.lease_expires_at = None
            await session.flush()


def _error_code(error: Exception) -> str:
    if isinstance(error, ApplicationError):
        return error.code
    for error_type, code in _ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return "internal_error"


_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (AccountNotFound, "account_not_found"),
    (AccountIdentityConflict, "account_identity_conflict"),
    (AccountDisabled, "account_disabled"),
    (AccountPaused, "account_paused"),
    (AccountRemoved, "account_removed"),
    (AuthenticationRequired, "authentication_required"),
    (PolicyUnavailable, "policy_unavailable"),
    (ReconciliationRequired, "reconciliation_required"),
    (PlanPreconditionFailed, "plan_precondition_failed"),
    (SessionRejected, "session_rejected"),
    (ContractChanged, "game_contract_changed"),
    (GameSchemaMismatch, "game_contract_changed"),
    (InventoryFull, "inventory_full"),
    (InsufficientResource, "insufficient_resource"),
    (GameConflict, "game_conflict"),
    (GameRateLimited, "game_rate_limited"),
    (AmbiguousMutation, "ambiguous_mutation"),
    (GameUnavailable, "game_unavailable"),
    (GameHttpError, "game_unavailable"),
)


class IdlePreviewScheduler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        accounts: AccountServicePort,
        idle_preview: IdlePreviewUseCasePort,
        *,
        store: IdlePreviewSchedulerStore | None = None,
        clock: Clock | None = None,
        worker_id: str,
        interval_seconds: int,
        lease_seconds: int,
        max_account_concurrency: int,
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must be a non-empty bounded identifier")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_account_concurrency <= 0:
            raise ValueError("max_account_concurrency must be positive")
        self.sessions = sessions
        self.accounts = accounts
        self.idle_preview = idle_preview
        self.store = store or PostgresIdlePreviewStore(sessions)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.lease_seconds = lease_seconds
        self._semaphore = asyncio.Semaphore(max_account_concurrency)
        self._closed = False
        self._close_event = asyncio.Event()

    async def tick(self, now: datetime | None = None) -> int:
        if self._closed:
            return 0
        current = now or self.clock()
        acquired = await self.store.acquire_lease(
            worker_id=self.worker_id,
            now=current,
            lease_seconds=self.lease_seconds,
        )
        if not acquired:
            return 0
        try:
            accounts = tuple(await self.accounts.list_accounts())
            await self.store.ensure_jobs(
                accounts,
                now=current,
                interval_seconds=self.interval_seconds,
            )
            eligible_ids = frozenset(
                account.id
                for account in accounts
                if account.enabled and account.paused_reason is None
            )
            claimed = await self.store.claim_due(
                now=current,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                account_ids=eligible_ids,
            )
            if not claimed:
                return 0
            await asyncio.gather(*(self._dispatch(run, current) for run in claimed))
            return len(claimed)
        finally:
            await self.store.release_lease(worker_id=self.worker_id)

    async def _dispatch(
        self, claimed: ClaimedIdlePreviewRun, now: datetime
    ) -> None:
        async with self._semaphore:
            correlation_id = uuid4().hex
            try:
                preview = await self.idle_preview.preview(
                    claimed.account_id,
                    actor=Actor("scheduler", self.worker_id),
                    correlation_id=correlation_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result: dict[str, object] = {
                    "status": "failed",
                    "error": _error_code(error),
                    "correlation_id": correlation_id,
                }
            else:
                result = {
                    "status": "succeeded",
                    "decision": preview.decision,
                    "plan_id": str(preview.plan_id) if preview.plan_id else None,
                    "correlation_id": correlation_id,
                }
            await self.store.finish_run(
                claimed,
                completed_at=now,
                next_run_at=now + timedelta(seconds=self.interval_seconds),
                result=result,
            )

    async def run(self, stop: asyncio.Event) -> None:
        while not self._closed and not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed tick must not make the application-wide loop unstoppable.
                pass
            if self._closed or stop.is_set():
                break
            await self._wait_for_next_tick(stop)

    async def _wait_for_next_tick(self, stop: asyncio.Event) -> None:
        stop_task = asyncio.create_task(stop.wait())
        close_task = asyncio.create_task(self._close_event.wait())
        try:
            done, _pending = await asyncio.wait(
                (stop_task, close_task),
                timeout=self.interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                return
        finally:
            for task in (stop_task, close_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_task, close_task, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        self._close_event.set()
