import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID, uuid4

from placegame.accounts.repository import AccountRepository
from placegame.accounts.service import LockedAccount
from placegame.contracts import Actor
from placegame.game.schemas import IdleSummary
from placegame.errors import AmbiguousMutation, PlanPreconditionFailed
from placegame.models import ActionPlan
from placegame.policy.models import VersionedPolicy
from placegame.policy.plans import (
    ActionPlanDraft,
    EstimatedCosts,
    IdleCollectAction,
    SelectedDecision,
    canonical_fingerprint,
)
from placegame.security.redaction import redact
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .errors import IdleReconciliationRequired, PlanInProgress
from .models import IdleExecution, IdlePreview


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")


class LockedAccountPort(Protocol):
    account_id: UUID
    api: object
    policy: VersionedPolicy


class AccountLockPort(Protocol):
    def locked(self, account_id: UUID, *, actor: Actor) -> AbstractAsyncContextManager[LockedAccount]: ...


class IdlePreviewStorePort(Protocol):
    async def save(
        self,
        draft: ActionPlanDraft | None,
        *,
        actor: Actor,
        correlation_id: str,
        preview: dict[str, object],
    ) -> UUID | None: ...


class IdlePreviewStore:
    """Persists a preview's optional plan and mandatory audit atomically."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], repository: AccountRepository) -> None:
        self.sessions = sessions
        self.repository = repository

    async def save(
        self,
        draft: ActionPlanDraft | None,
        *,
        actor: Actor,
        correlation_id: str,
        preview: dict[str, object],
    ) -> UUID | None:
        _require_identifier(correlation_id)
        actor_value, source = _audit_actor(actor)
        async with self.sessions.begin() as session:
            plan_id: UUID | None = None
            if draft is not None:
                row = ActionPlan(
                    account_id=draft.account_id,
                    state_fingerprint=draft.state_fingerprint,
                    policy_version=draft.policy_version,
                    proposed_actions=[decision.model_dump(mode="json", by_alias=True) for decision in draft.decisions],
                    estimated_costs=draft.estimated_costs.model_dump(mode="json"),
                    risk=draft.risk,
                    expires_at=draft.expires_at,
                    confirmation_required=draft.confirmation_required,
                    execution_state="pending",
                )
                session.add(row)
                await session.flush()
                plan_id = row.id
            if draft is not None:
                audit_account_id = draft.account_id
            else:
                raw_account_id = preview.get("accountId")
                if not isinstance(raw_account_id, str):
                    raise ValueError("preview account ID is required")
                audit_account_id = UUID(raw_account_id)
            await self.repository.add_audit(
                session,
                actor=actor_value,
                source=source,
                account_id=audit_account_id,
                plan_id=plan_id,
                action="idle.preview",
                result=redact(preview),
                correlation_id=correlation_id,
            )
            return plan_id


class IdleExecutionGuard:
    """A session advisory lock intentionally separate from account transactions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def hold(self, account_id: UUID):
        async with self.sessions() as session:
            result = await session.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:account_id, 2))"),
                {"account_id": str(account_id)},
            )
            if not bool(result.scalar()):
                raise PlanInProgress() from None
            try:
                yield
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:account_id, 2))"),
                    {"account_id": str(account_id)},
                )
                await session.commit()


class _Claim:
    def __init__(self, owner: str, plan: ActionPlan, recovery: bool) -> None:
        self.owner = owner
        self.plan = plan
        self.recovery = recovery


class IdleExecutionClaims:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: AccountRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.sessions = sessions
        self.repository = repository
        self.clock = clock

    async def claim(
        self,
        account_id: UUID,
        plan_id: UUID,
        *,
        actor: Actor,
        correlation_id: str,
        recovery: bool,
    ) -> _Claim:
        _require_identifier(correlation_id)
        owner = uuid4().hex
        now = self.clock()
        actor_value, source = _audit_actor(actor)
        async with self.sessions.begin() as session:
            plan = await session.scalar(
                select(ActionPlan)
                .where(ActionPlan.id == plan_id, ActionPlan.account_id == account_id)
                .with_for_update()
            )
            if plan is None:
                raise PlanPreconditionFailed() from None
            if plan.execution_state == "executing":
                lease = plan.execution_lease_expires_at
                if lease is not None and lease > now:
                    raise PlanInProgress() from None
                if not recovery:
                    raise PlanInProgress() from None
                plan.execution_owner = owner
                plan.execution_started_at = now
                plan.execution_lease_expires_at = now + timedelta(minutes=2)
                await self._audit_claim(session, actor_value, source, account_id, plan_id, correlation_id, "recovery")
                return _Claim(owner, plan, True)
            self._validate_fresh(plan, now)
            plan.execution_state = "executing"
            plan.execution_owner = owner
            plan.execution_started_at = now
            plan.execution_lease_expires_at = now + timedelta(minutes=2)
            plan.execution_attempt_count += 1
            await self._audit_claim(session, actor_value, source, account_id, plan_id, correlation_id, "claimed")
            return _Claim(owner, plan, False)

    @staticmethod
    def _validate_fresh(plan: ActionPlan, now: datetime) -> None:
        if plan.execution_state not in {"pending", "confirmed"} or plan.expires_at <= now:
            raise PlanPreconditionFailed() from None
        if plan.risk != "low" or plan.confirmation_required:
            raise PlanPreconditionFailed() from None
        try:
            decisions = plan.proposed_actions
            decision = decisions[0] if len(decisions) == 1 else None
            if not isinstance(decision, dict) or decision.get("family") != "idle" or decision.get("state") != "selected" or decision.get("action") != {"family": "idle", "kind": "idle_collect"}:
                raise ValueError
        except (TypeError, ValueError):
            raise PlanPreconditionFailed() from None

    async def _audit_claim(self, session, actor, source, account_id, plan_id, correlation_id, status) -> None:
        await self.repository.add_audit(
            session,
            actor=actor,
            source=source,
            account_id=account_id,
            plan_id=plan_id,
            action="idle.execute.claim",
            result={"status": status},
            correlation_id=correlation_id,
        )

    async def owned(self, account_id: UUID, plan_id: UUID, owner: str) -> bool:
        async with self.sessions() as session:
            plan = await session.scalar(select(ActionPlan).where(ActionPlan.id == plan_id, ActionPlan.account_id == account_id))
            return plan is not None and plan.execution_state == "executing" and plan.execution_owner == owner

    async def finish(
        self,
        claim: _Claim,
        *,
        status: str,
        actor: Actor,
        correlation_id: str,
        result: dict[str, object],
    ) -> None:
        actor_value, source = _audit_actor(actor)
        async with self.sessions.begin() as session:
            plan = await session.scalar(select(ActionPlan).where(ActionPlan.id == claim.plan.id).with_for_update())
            if plan is None or plan.execution_state != "executing" or plan.execution_owner != claim.owner:
                raise PlanPreconditionFailed() from None
            plan.execution_state = status
            plan.execution_result = redact(result)
            plan.executed_at = self.clock()
            await self.repository.add_audit(
                session,
                actor=actor_value,
                source=source,
                account_id=plan.account_id,
                plan_id=plan.id,
                action="idle.execute.finish",
                result=redact(result),
                correlation_id=correlation_id,
            )


class IdlePlanner:
    """Pure idle decision and fingerprint rules shared by preview and execution."""

    @staticmethod
    def threshold(policy: VersionedPolicy, idle: IdleSummary) -> int:
        configured = policy.idle_threshold_minutes * 60
        if idle.capacity_seconds is None:
            # The live game reports no ceiling, and it already reports only the
            # seconds it considers collectible. Clamping to a guessed cap would
            # make collection fire earlier than the operator asked for.
            return configured
        return min(configured, idle.capacity_seconds)

    def decision(self, idle: IdleSummary, policy: VersionedPolicy) -> Literal["collect", "wait"]:
        return "collect" if idle.accumulated_seconds >= self.threshold(policy, idle) else "wait"

    def fingerprint(self, idle: IdleSummary, policy: VersionedPolicy) -> str:
        eligible = self.decision(idle, policy) == "collect"
        return canonical_fingerprint(
            "idle",
            {"capacitySeconds": idle.capacity_seconds, "eligible": eligible},
        )

    def draft(
        self,
        account_id: UUID,
        idle: IdleSummary,
        policy: VersionedPolicy,
        *,
        now: datetime,
    ) -> ActionPlanDraft | None:
        if self.decision(idle, policy) != "collect":
            return None
        return ActionPlanDraft(
            account_id=account_id,
            state_fingerprint=self.fingerprint(idle, policy),
            policy_version=policy.version,
            family="idle",
            proposedActions=[
                SelectedDecision(
                    family="idle",
                    reason="idle_threshold_reached",
                    action=IdleCollectAction(),
                )
            ],
            estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
            risk="low",
            expires_at=now + timedelta(minutes=5),
            confirmation_required=False,
        )


class IdlePlanUseCase:
    def __init__(
        self,
        accounts: AccountLockPort,
        previews: IdlePreviewStorePort,
        *,
        planner: IdlePlanner | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.accounts = accounts
        self.previews = previews
        self.planner = planner or IdlePlanner()
        self.clock = clock

    async def preview(self, account_id: UUID, *, actor: Actor, correlation_id: str) -> IdlePreview:
        _require_identifier(correlation_id)
        async with self.accounts.locked(account_id, actor=actor) as locked:
            idle = await locked.api.idle_summary()  # type: ignore[attr-defined]
            threshold = self.planner.threshold(locked.policy, idle)
            decision = self.planner.decision(idle, locked.policy)
            draft = self.planner.draft(account_id, idle, locked.policy, now=self.clock())
            preview = {
                "decision": decision,
                "accumulatedSeconds": idle.accumulated_seconds,
                "capacitySeconds": idle.capacity_seconds,
                "thresholdSeconds": threshold,
                "reason": "idle_threshold_reached" if decision == "collect" else "idle_threshold_not_reached",
                "accountId": str(account_id),
            }
        plan_id = await self.previews.save(
            draft,
            actor=actor,
            correlation_id=correlation_id,
            preview=preview,
        )
        return IdlePreview(
            account_id=account_id,
            plan_id=plan_id,
            decision=decision,
            accumulated_seconds=idle.accumulated_seconds,
            capacity_seconds=idle.capacity_seconds,
            threshold_seconds=threshold,
            expires_at=draft.expires_at if draft is not None else None,
            reason=preview["reason"],
            correlation_id=correlation_id,
        )


def _require_identifier(value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier must be an ASCII identifier up to 128 characters")


def _audit_actor(actor: Actor) -> tuple[str, str]:
    kind = getattr(actor, "kind", None)
    actor_id = getattr(actor, "actor_id", None)
    if not isinstance(kind, str) or not isinstance(actor_id, str):
        raise ValueError("actor is required")
    _require_identifier(kind)
    _require_identifier(actor_id)
    value = f"{kind}:{actor_id}"
    if len(value) > 128:
        raise ValueError("actor identifier is too long")
    return value, kind


class IdleExecuteUseCase:
    def __init__(
        self,
        accounts: AccountLockPort,
        guard: IdleExecutionGuard,
        claims: IdleExecutionClaims,
        *,
        planner: IdlePlanner | None = None,
    ) -> None:
        self.accounts = accounts
        self.guard = guard
        self.claims = claims
        self.planner = planner or IdlePlanner()

    async def execute(
        self,
        account_id: UUID,
        plan_id: UUID,
        *,
        actor: Actor,
        correlation_id: str,
        recovery: bool = False,
    ) -> IdleExecution:
        _require_identifier(correlation_id)
        async with self.guard.hold(account_id):
            claim = await self.claims.claim(
                account_id,
                plan_id,
                actor=actor,
                correlation_id=correlation_id,
                recovery=recovery,
            )
            if claim.recovery:
                return await self._recover(account_id, claim, actor, correlation_id)
            return await self._send_once(account_id, claim, actor, correlation_id)

    async def _send_once(self, account_id: UUID, claim: _Claim, actor: Actor, correlation_id: str) -> IdleExecution:
        async with self.accounts.locked(account_id, actor=actor) as locked:
            before = await locked.api.idle_summary()  # type: ignore[attr-defined]
            if (
                not await self.claims.owned(account_id, claim.plan.id, claim.owner)
                or locked.policy.version != claim.plan.policy_version
                or self.planner.fingerprint(before, locked.policy) != claim.plan.state_fingerprint
            ):
                outcome = "precondition_failed"
            else:
                try:
                    response = await locked.api.idle_collect()  # type: ignore[attr-defined]
                    after = await locked.api.idle_summary()  # type: ignore[attr-defined]
                except Exception:
                    try:
                        current = await locked.api.idle_summary()  # type: ignore[attr-defined]
                    except Exception:
                        outcome = "reconciliation_required"
                    else:
                        outcome = (
                            "reconciled"
                            if locked.policy.version == claim.plan.policy_version
                            and self.planner.decision(current, locked.policy) == "wait"
                            else "reconciliation_required"
                        )
                else:
                    outcome = (
                        "reconciliation_required"
                        if not response.collected or after.accumulated_seconds >= before.accumulated_seconds
                        else "executed"
                    )
        if outcome == "precondition_failed":
            await self.claims.finish(
                claim,
                status="failed",
                actor=actor,
                correlation_id=correlation_id,
                result={"status": "precondition_failed"},
            )
            raise PlanPreconditionFailed() from None
        if outcome == "reconciliation_required":
            return await self._require_reconciliation(claim, actor, correlation_id)
        if outcome == "reconciled":
            await self.claims.finish(
                claim,
                status="executed",
                actor=actor,
                correlation_id=correlation_id,
                result={"status": "executed", "reconciled": True, "collected": True},
            )
            return IdleExecution(account_id=account_id, plan_id=claim.plan.id, status="reconciled", applied=True, reconciled=True, collected=True, correlation_id=correlation_id)
        await self.claims.finish(
            claim,
            status="executed",
            actor=actor,
            correlation_id=correlation_id,
            result={"status": "executed", "reconciled": False, "collected": True},
        )
        return IdleExecution(
            account_id=account_id,
            plan_id=claim.plan.id,
            status="executed",
            applied=True,
            reconciled=False,
            collected=True,
            correlation_id=correlation_id,
        )

    async def _recover(self, account_id: UUID, claim: _Claim, actor: Actor, correlation_id: str) -> IdleExecution:
        async with self.accounts.locked(account_id, actor=actor) as locked:
            current = await locked.api.idle_summary()  # type: ignore[attr-defined]
            proven = (
                locked.policy.version == claim.plan.policy_version
                and self.planner.decision(current, locked.policy) == "wait"
            )
        if not proven:
            return await self._require_reconciliation(claim, actor, correlation_id)
        await self.claims.finish(
            claim,
            status="executed",
            actor=actor,
            correlation_id=correlation_id,
            result={"status": "executed", "reconciled": True, "collected": True},
        )
        return IdleExecution(account_id=account_id, plan_id=claim.plan.id, status="reconciled", applied=False, reconciled=True, collected=True, correlation_id=correlation_id)

    async def _require_reconciliation(self, claim: _Claim, actor: Actor, correlation_id: str) -> IdleExecution:
        await self.claims.finish(
            claim,
            status="reconciliation_required",
            actor=actor,
            correlation_id=correlation_id,
            result={"status": "reconciliation_required"},
        )
        raise IdleReconciliationRequired() from None
