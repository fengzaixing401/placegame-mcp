from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

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


Clock = Callable[[], datetime]


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


class _UnavailableStore:
    """Makes an omitted store fail explicitly until the durable store is wired."""

    async def acquire_lease(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> bool:
        del worker_id, now, lease_seconds
        raise RuntimeError("idle preview scheduler store is not configured")

    async def ensure_jobs(
        self,
        accounts: Sequence[SchedulerAccount],
        *,
        now: datetime,
        interval_seconds: int,
    ) -> None:
        del accounts, now, interval_seconds
        raise RuntimeError("idle preview scheduler store is not configured")

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
        account_ids: frozenset[UUID],
    ) -> tuple[ClaimedIdlePreviewRun, ...]:
        del now, worker_id, lease_seconds, account_ids
        raise RuntimeError("idle preview scheduler store is not configured")

    async def finish_run(
        self,
        claimed: ClaimedIdlePreviewRun,
        *,
        completed_at: datetime,
        next_run_at: datetime,
        result: dict[str, object],
    ) -> None:
        del claimed, completed_at, next_run_at, result
        raise RuntimeError("idle preview scheduler store is not configured")

    async def release_lease(self, *, worker_id: str) -> None:
        del worker_id


def idle_preview_idempotency_key(account_id: UUID, scheduled_for: datetime) -> str:
    """Return a stable, bounded key for one account and scheduled slot."""

    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    slot = scheduled_for.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return f"idle_preview:{account_id}:{slot}"


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
        self.store = store or _UnavailableStore()
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
