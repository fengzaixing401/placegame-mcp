from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from placegame.contracts import Actor
from placegame.errors import PlanPreconditionFailed
from placegame.models import ActionPlan

ActionFamily = Literal["idle", "personal_boss", "ordinary_boss", "world_boss", "profession", "safe_reward"]
DecisionState = Literal["selected", "skipped", "blocked"]
PlanState = Literal["pending", "confirmed", "executing", "executed", "failed", "reconciliation_required"]
TerminalPlanState = Literal["executed", "failed", "reconciliation_required"]
RiskClass = Literal["low", "medium", "high"]
ExecutionResultValue = StrictStr | StrictBool | StrictInt
ExecutionResult = dict[str, ExecutionResultValue]

class EstimatedCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    material: int = Field(0, ge=0)
    attempts: int = Field(0, ge=0)
    currency: int = Field(0, ge=0)

class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

class IdleCollectAction(_Action):
    family: Literal["idle"] = "idle"
    kind: Literal["idle_collect"] = "idle_collect"

class BossChallengeAction(_Action):
    family: Literal["personal_boss", "ordinary_boss"] = "personal_boss"
    kind: Literal["boss_challenge"] = "boss_challenge"
    boss_key: str = Field(alias="bossKey", min_length=1)
    difficulty: Literal["normal", "hard", "nightmare"]
    selected_skill_keys: list[str] = Field(alias="selectedSkillKeys", max_length=3)
    buff_key: Literal["none", "assault", "guard", "focus"] = Field(alias="buffKey")
    affix_key: str | None = Field(alias="affixKey")
    target_slot: str = Field(alias="targetSlot", min_length=1)
    use_material_boost: bool = Field(alias="useMaterialBoost")

class BossAssistAction(_Action):
    family: Literal["world_boss"] = "world_boss"
    kind: Literal["boss_assist"] = "boss_assist"
    boss_key: str = Field(alias="bossKey", min_length=1)

class ProfessionSettleAction(_Action):
    family: Literal["profession"] = "profession"
    kind: Literal["profession_settle"] = "profession_settle"

class ProfessionEnqueueAction(_Action):
    family: Literal["profession"] = "profession"
    kind: Literal["profession_enqueue"] = "profession_enqueue"
    action_key: str = Field(alias="actionKey", min_length=1)
    count: int = Field(ge=1)

class ProfessionSupplyEquipAction(_Action):
    family: Literal["profession"] = "profession"
    kind: Literal["profession_supply_equip"] = "profession_supply_equip"
    supply_type: str = Field(alias="supplyType", min_length=1)
    item_key: str = Field(alias="itemKey", min_length=1)

class DailyClaimAction(_Action):
    family: Literal["safe_reward"] = "safe_reward"
    kind: Literal["daily_claim"] = "daily_claim"
    point: int = Field(ge=0)

class QuestClaimAction(_Action):
    family: Literal["safe_reward"] = "safe_reward"
    kind: Literal["quest_claim"] = "quest_claim"
    quest_key: str = Field(alias="questKey", min_length=1)

class AchievementClaimAction(_Action):
    family: Literal["safe_reward"] = "safe_reward"
    kind: Literal["achievement_claim"] = "achievement_claim"
    achievement_key: str = Field(alias="achievementKey", min_length=1)

class CodexClaimAction(_Action):
    family: Literal["safe_reward"] = "safe_reward"
    kind: Literal["codex_claim"] = "codex_claim"
    reward_key: str = Field(alias="rewardKey", min_length=1)

class MailClaimAction(_Action):
    family: Literal["safe_reward"] = "safe_reward"
    kind: Literal["mail_claim"] = "mail_claim"
    mail_id: str = Field(alias="mailId", min_length=1)

RegisteredAction = Annotated[IdleCollectAction | BossChallengeAction | BossAssistAction | ProfessionSettleAction | ProfessionEnqueueAction | ProfessionSupplyEquipAction | DailyClaimAction | QuestClaimAction | AchievementClaimAction | CodexClaimAction | MailClaimAction, Field(discriminator="kind")]

class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    family: ActionFamily
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")


class SelectedDecision(_Decision):
    state: Literal["selected"] = "selected"
    action: RegisteredAction

    @model_validator(mode="after")
    def validate_action_family(self) -> SelectedDecision:
        if self.action.family != self.family:
            raise ValueError("decision family must match action family")
        return self


class SkippedDecision(_Decision):
    state: Literal["skipped"] = "skipped"
    action: RegisteredAction | None = None

    @model_validator(mode="after")
    def validate_action_family(self) -> SkippedDecision:
        if self.action is not None and self.action.family != self.family:
            raise ValueError("decision family must match action family")
        return self


class BlockedDecision(_Decision):
    state: Literal["blocked"] = "blocked"
    action: RegisteredAction | None = None

    @model_validator(mode="after")
    def validate_action_family(self) -> BlockedDecision:
        if self.action is not None and self.action.family != self.family:
            raise ValueError("decision family must match action family")
        return self

Decision = Annotated[SelectedDecision | SkippedDecision | BlockedDecision, Field(discriminator="state")]

def _normalize(value: object) -> object:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError("non-finite float is not canonical JSON")
        raise TypeError("float is not canonical JSON")
    if type(value) is dict:
        if not all(isinstance(k, str) for k in value):
            raise TypeError("mapping keys must be strings")
        return {k: _normalize(v) for k, v in value.items()}
    if type(value) is list:
        return [_normalize(v) for v in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")

def canonical_json(value: object) -> bytes:
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def _normalize_projection(value: object, keyed: Mapping[str, str], path: str = "") -> object:
    if type(value) is dict:
        if not all(isinstance(k, str) for k in value):
            raise TypeError("mapping keys must be strings")
        return {k: _normalize_projection(v, keyed, f"{path}.{k}" if path else k) for k, v in sorted(value.items())}
    if type(value) is list:
        vals = [_normalize_projection(v, keyed, path) for v in value]
        field = keyed.get(path, keyed.get(path.rsplit(".", 1)[-1]))
        if field is not None:
            if not all(type(v) is dict and isinstance(v.get(field), str) for v in vals):
                raise TypeError("keyed arrays require string keys")
            vals.sort(key=lambda v: v[field])  # type: ignore[index]
        return vals
    return _normalize(value)

def canonical_fingerprint(family: ActionFamily, projection: Mapping[str, object], *, keyed_arrays: Mapping[str, str] | None = None) -> str:
    return "pgfp:v1:" + hashlib.sha256(canonical_json({"family": family, "projection": _normalize_projection(projection, keyed_arrays or {})})).hexdigest()


def _default_estimated_costs() -> EstimatedCosts:
    return EstimatedCosts(material=0, attempts=0, currency=0)


class _PlanDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    account_id: UUID
    state_fingerprint: str = Field(pattern=r"^pgfp:v1:[0-9a-f]{64}$")
    policy_version: int = Field(ge=1)
    decisions: list[Decision] = Field(alias="proposedActions", min_length=1)
    estimated_costs: EstimatedCosts = Field(default_factory=_default_estimated_costs, alias="estimatedCosts")
    risk: RiskClass
    expires_at: datetime
    confirmation_required: bool = True

    @model_validator(mode="after")
    def validate_decision_families(self) -> _PlanDetails:
        if len({decision.family for decision in self.decisions}) != 1:
            raise ValueError("plan decisions must have exactly one family")
        return self


class TypedActionPlan(_PlanDetails):
    id: UUID = Field(default_factory=uuid4)
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    execution_state: PlanState = "pending"
    executed_at: datetime | None = None
    execution_result: ExecutionResult | None = None
    execution_owner: str | None = None
    execution_started_at: datetime | None = None
    execution_lease_expires_at: datetime | None = None
    execution_attempt_count: int = Field(default=0, ge=0)

    @property
    def family(self) -> ActionFamily:
        return self.decisions[0].family

    @model_validator(mode="after")
    def validate_plan(self) -> TypedActionPlan:
        self.family
        if (self.confirmed_at is None) != (self.confirmed_by is None):
            raise ValueError("complete confirmation metadata is required")
        if self.execution_state == "confirmed" and self.confirmed_at is None:
            raise ValueError("confirmed plans require confirmation metadata")
        if self.execution_result is not None:
            if len(self.execution_result) > 16:
                raise ValueError("execution result has too many fields")
            for key, value in self.execution_result.items():
                if len(key) > 64:
                    raise ValueError("execution result key is too long")
                if isinstance(value, str) and len(value) > 256:
                    raise ValueError("execution result value is too long")
        return self

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> TypedActionPlan:
        return cls.model_validate(value)

class ActionPlanDraft(_PlanDetails):
    family: ActionFamily

    @model_validator(mode="after")
    def validate_declared_family(self) -> ActionPlanDraft:
        if any(decision.family != self.family for decision in self.decisions):
            raise ValueError("draft family must match every decision")
        return self

class PlanStore(Protocol):
    async def create(self, draft: ActionPlanDraft) -> TypedActionPlan: ...
    async def get_for_update(self, plan_id: UUID, account_id: UUID) -> TypedActionPlan: ...
    async def confirm(self, plan_id: UUID, account_id: UUID, *, actor: Actor) -> TypedActionPlan: ...
    async def mark_executing(self, plan_id: UUID, expected_state: Literal["pending", "confirmed"]) -> None: ...
    async def finish(self, plan_id: UUID, status: TerminalPlanState, result: Mapping[str, object]) -> None: ...

class PostgresPlanStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _typed(row: ActionPlan) -> TypedActionPlan:
        return TypedActionPlan.model_validate({"id": row.id, "account_id": row.account_id, "state_fingerprint": row.state_fingerprint, "policy_version": row.policy_version, "proposedActions": row.proposed_actions, "estimatedCosts": row.estimated_costs, "risk": row.risk, "expires_at": row.expires_at, "confirmation_required": row.confirmation_required, "confirmed_at": row.confirmed_at, "confirmed_by": row.confirmed_by, "execution_state": row.execution_state, "executed_at": row.executed_at, "execution_result": row.execution_result, "execution_owner": row.execution_owner, "execution_started_at": row.execution_started_at, "execution_lease_expires_at": row.execution_lease_expires_at, "execution_attempt_count": row.execution_attempt_count})

    async def create(self, draft: ActionPlanDraft) -> TypedActionPlan:
        typed = ActionPlanDraft.model_validate(draft)
        row = ActionPlan(
            account_id=typed.account_id,
            state_fingerprint=typed.state_fingerprint,
            policy_version=typed.policy_version,
            proposed_actions=[
                decision.model_dump(mode="json", by_alias=True)
                for decision in typed.decisions
            ],
            estimated_costs=typed.estimated_costs.model_dump(mode="json"),
            risk=typed.risk,
            expires_at=typed.expires_at,
            confirmation_required=typed.confirmation_required,
            execution_state="pending",
            confirmed_at=None,
            confirmed_by=None,
            executed_at=None,
            execution_result=None,
        )
        self.session.add(row)
        await self.session.flush()
        return self._typed(row)

    async def get_for_update(self, plan_id: UUID, account_id: UUID) -> TypedActionPlan:
        row = await self.session.scalar(
            select(ActionPlan)
            .where(ActionPlan.id == plan_id, ActionPlan.account_id == account_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None:
            raise PlanPreconditionFailed() from None
        return self._typed(row)

    async def confirm(self, plan_id: UUID, account_id: UUID, *, actor: Actor) -> TypedActionPlan:
        row = await self.session.scalar(select(ActionPlan).where(ActionPlan.id == plan_id, ActionPlan.account_id == account_id).with_for_update())
        if (
            row is None
            or row.execution_state != "pending"
            or row.expires_at <= datetime.now(timezone.utc)
        ):
            raise PlanPreconditionFailed() from None
        row.execution_state = "confirmed"
        row.confirmed_at = datetime.now(timezone.utc)
        actor_id = "".join(c if c.isascii() and (c.isalnum() or c in "_.:@-") else "_" for c in actor.actor_id)
        row.confirmed_by = f"{actor.kind}:{actor_id}"[:128]
        await self.session.flush()
        return self._typed(row)

    async def mark_executing(self, plan_id: UUID, expected_state: Literal["pending", "confirmed"]) -> None:
        if expected_state not in ("pending", "confirmed"):
            raise PlanPreconditionFailed() from None
        row = await self.session.scalar(select(ActionPlan).where(ActionPlan.id == plan_id).with_for_update())
        if (
            row is None
            or row.execution_state != expected_state
            or row.expires_at <= datetime.now(timezone.utc)
            or (expected_state == "pending" and row.confirmation_required)
        ):
            raise PlanPreconditionFailed() from None
        row.execution_state = "executing"
        await self.session.flush()

    async def finish(self, plan_id: UUID, status: TerminalPlanState, result: Mapping[str, object]) -> None:
        if status not in ("executed", "failed", "reconciliation_required"):
            raise PlanPreconditionFailed() from None
        row = await self.session.scalar(select(ActionPlan).where(ActionPlan.id == plan_id).with_for_update())
        if row is None:
            raise PlanPreconditionFailed() from None
        if row.execution_state in ("executed", "failed", "reconciliation_required"):
            raise PlanPreconditionFailed() from None
        if row.execution_state not in ("pending", "confirmed", "executing"):
            raise PlanPreconditionFailed() from None
        safe: dict[str, object] = {}
        for key, value in result.items():
            if isinstance(key, str) and len(safe) < 16 and isinstance(value, (str, bool, int)):
                safe[key[:64]] = value[:256] if isinstance(value, str) else value
        row.execution_state = status
        row.execution_result = safe
        row.executed_at = datetime.now(timezone.utc)
        await self.session.flush()
