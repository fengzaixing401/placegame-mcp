from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from placegame.accounts.service import LockedAccount, StateFingerprintResolver
from placegame.application.idle import IdlePlanner
from placegame.boss_optimizer import BossOptimizer, BossSelection
from placegame.errors import GameSchemaMismatch
from placegame.game.client import GameApi
from placegame.game.schemas import BossState, IdleSummary, ProfessionState, WorldBossState
from placegame.policy.models import VersionedPolicy
from placegame.policy.plans import (
    BlockedDecision,
    BossAssistAction,
    BossChallengeAction,
    EstimatedCosts,
    IdleCollectAction,
    ProfessionEnqueueAction,
    ProfessionSettleAction,
    SelectedDecision,
    SkippedDecision,
    TypedActionPlan,
    ActionFamily,
    Decision,
    RiskClass,
    canonical_fingerprint,
)
from placegame.rewards import SafeRewardPlanner


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _decimal_text(value: float) -> str:
    text = format(Decimal(str(value)).normalize(), "f")
    return "0" if Decimal(text) == 0 else text


class PolicyEngine:
    async def build_idle_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan:
        summary = await locked.api.idle_summary()
        planner = IdlePlanner()
        if planner.decision(summary, locked.policy) == "collect":
            decision = SelectedDecision(
                family="idle", reason="idle_threshold_reached", action=IdleCollectAction()
            )
        else:
            decision = SkippedDecision(family="idle", reason="idle_threshold_not_reached")
        return self._plan(
            locked,
            "idle",
            [decision],
            {
                "capacitySeconds": summary.capacity_seconds,
                "eligible": planner.decision(summary, locked.policy) == "collect",
            },
            now,
        )

    async def build_personal_boss_plan(
        self, locked: LockedAccount, *, now: datetime
    ) -> TypedActionPlan:
        state = await locked.api.boss_state()
        if state.free_attempts == 0 and not locked.policy.personal_paid_attempts:
            return self._plan(
                locked,
                "personal_boss",
                [BlockedDecision(family="personal_boss", reason="personal_free_attempts_exhausted")],
                self.boss_projection(state, None),
                now,
            )
        personal = state.model_copy(
            update={"entries": [entry for entry in state.entries if entry.type == "personal"]}
        )
        if not personal.entries:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        selection = await BossOptimizer(locked.api).optimize(personal, locked.policy)
        return self._plan(
            locked,
            "personal_boss",
            [selection.decision],
            self.boss_projection(personal, selection),
            now,
            risk="high",
        )

    async def build_ordinary_boss_plan(
        self, locked: LockedAccount, *, now: datetime
    ) -> TypedActionPlan:
        state = await locked.api.boss_state()
        candidates = [
            entry
            for entry in state.entries
            if entry.type in {"map", "world"}
            and entry.attempts is not None
            and entry.attempts > 0
            and entry.blocked_reason is None
        ]
        if not candidates:
            return self._plan(
                locked,
                "ordinary_boss",
                [SkippedDecision(family="ordinary_boss", reason="no_ordinary_boss_available")],
                self.boss_projection(state, None),
                now,
            )
        selection = await BossOptimizer(
            locked.api
        ).optimize(state.model_copy(update={"entries": candidates}), locked.policy)
        decision = selection.decision
        if selection.action is not None:
            action = BossChallengeAction(
                family="ordinary_boss",
                bossKey=selection.action.boss_key,
                difficulty=selection.action.difficulty,
                selectedSkillKeys=selection.action.selected_skill_keys,
                buffKey=selection.action.buff_key,
                affixKey=selection.action.affix_key,
                targetSlot=selection.action.target_slot,
                useMaterialBoost=selection.action.use_material_boost,
            )
            decision = SelectedDecision(
                family="ordinary_boss", reason="ordinary_boss_eligible", action=action
            )
        elif decision.state == "blocked":
            decision = BlockedDecision(family="ordinary_boss", reason=decision.reason)
        return self._plan(
            locked,
            "ordinary_boss",
            [decision],
            self.boss_projection(state.model_copy(update={"entries": candidates}), selection),
            now,
            risk="high",
        )

    async def build_world_boss_plan(
        self, locked: LockedAccount, *, now: datetime
    ) -> TypedActionPlan:
        state = await locked.api.world_boss_state()
        if not locked.policy.world_collaboration_enabled:
            decision = SkippedDecision(family="world_boss", reason="world_collaboration_disabled")
        elif not self.world_window(now):
            decision = SkippedDecision(family="world_boss", reason="world_window_closed")
        else:
            eligible = next(
                (
                    item
                    for item in sorted(state.instances, key=lambda item: item.key)
                    if item.active
                    and item.alive
                    and item.my_attempt_count < locked.policy.world_attempts
                    and item.remaining_attempt_count > 0
                ),
                None,
            )
            decision = (
                SelectedDecision(
                    family="world_boss",
                    reason="world_assist_eligible",
                    action=BossAssistAction(bossKey=eligible.key),
                )
                if eligible is not None
                else SkippedDecision(family="world_boss", reason="world_attempts_unavailable")
            )
        return self._plan(
            locked, "world_boss", [decision], self.world_projection(state), now, risk="medium"
        )

    async def build_profession_plan(
        self, locked: LockedAccount, *, now: datetime
    ) -> TypedActionPlan:
        state = await locked.api.profession_state()
        if now.astimezone(SHANGHAI).minute % 5 == 0:
            decisions = [
                SelectedDecision(
                    family="profession", reason="profession_maintenance_due", action=ProfessionSettleAction()
                )
            ]
        else:
            action = self._profession_action(state, locked.policy)
            decisions = (
                [SelectedDecision(family="profession", reason="profession_queue_refill", action=action)]
                if action is not None
                else [SkippedDecision(family="profession", reason="profession_queue_sufficient")]
            )
        return self._plan(
            locked, "profession", decisions, self.profession_projection(state), now, risk="low"
        )

    async def safe_reward_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan:
        state = await locked.api.reward_state()
        return SafeRewardPlanner().build(
            state, locked.policy, account_id=locked.account_id, now=now
        )

    @staticmethod
    def world_window(now: datetime) -> bool:
        local = now.astimezone(SHANGHAI)
        return (local.hour, local.minute) >= (10, 0) and (local.hour, local.minute) < (11, 0) or (local.hour, local.minute) >= (16, 0) and (local.hour, local.minute) < (17, 0) or (local.hour, local.minute) >= (20, 0) and (local.hour, local.minute) < (21, 0)

    def idle_resolver(self) -> StateFingerprintResolver:
        async def resolve(api: GameApi) -> str:
            return canonical_fingerprint("idle", self.idle_projection(await api.idle_summary()))
        return resolve

    @staticmethod
    def idle_projection(summary: IdleSummary) -> dict[str, object]:
        return {"accumulatedSeconds": summary.accumulated_seconds, "capacitySeconds": summary.capacity_seconds}

    @staticmethod
    def boss_projection(state: BossState, selection: BossSelection | None) -> dict[str, object]:
        data: dict[str, object] = {
            "entries": [
                {"key": entry.key, "type": entry.type, "requiredLevel": entry.required_level, "attempts": entry.attempts, "blockedReason": entry.blocked_reason, "refreshKey": entry.refresh_key, "difficultyOptions": entry.difficulty_options}
                for entry in state.entries
            ],
            "freeAttempts": state.free_attempts,
            "materialBalance": state.material_balance,
            "equipment": [{"key": slot.key, "score": slot.score, "eligible": slot.eligible} for slot in state.equipment],
            "potion": {"activeKey": state.potion.active_key, "requiredKey": state.potion.required_key},
            "affixes": [{"key": affix.key, "multiplier": _decimal_text(affix.multiplier)} for affix in state.affixes],
        }
        if selection is not None and selection.action is not None and selection.preview is not None:
            data["selection"] = {
                "bossKey": selection.action.boss_key,
                "difficulty": selection.action.difficulty,
                "skills": selection.action.selected_skill_keys,
                "buffKey": selection.action.buff_key,
                "affixKey": selection.action.affix_key,
                "targetSlot": selection.action.target_slot,
                "materialBoost": selection.action.use_material_boost,
                "preview": {
                    "predictedWin": selection.preview.predicted_win,
                    "chance": _decimal_text(selection.preview.chance),
                    "playerHpRemainingPercent": _decimal_text(selection.preview.player_hp_remaining_percent),
                    "bossHpRemainingPercent": _decimal_text(selection.preview.boss_hp_remaining_percent),
                },
            }
        return data

    @staticmethod
    def world_projection(state: WorldBossState) -> dict[str, object]:
        return {"instances": [{"key": item.key, "lifecycle": item.lifecycle, "active": item.active, "alive": item.alive, "myAttemptCount": item.my_attempt_count, "remainingAttemptCount": item.remaining_attempt_count} for item in state.instances]}

    @staticmethod
    def profession_projection(state: ProfessionState) -> dict[str, object]:
        return {"selectedProfessionKey": state.selected_profession_key, "queue": [{"key": item.action_key, "remainingSeconds": item.remaining_seconds} for item in state.queue], "unlockProgress": state.unlock_progress, "recipes": [{"key": item.key, "durationSeconds": item.duration_seconds, "outputKey": item.output_key, "outputCount": item.output_count, "requiredInputs": item.required_inputs, "unlockMilestone": item.unlock_milestone} for item in state.recipes], "balances": state.balances, "recipeVersion": state.recipe_version}

    @staticmethod
    def _profession_action(state: ProfessionState, policy: VersionedPolicy) -> ProfessionEnqueueAction | None:
        queued_seconds = sum(item.remaining_seconds for item in state.queue)
        if len(state.queue) >= 2 and queued_seconds >= 6 * 3600:
            return None
        capacity = 5 - len(state.queue)
        if capacity <= 0:
            return None
        recipes = sorted(state.recipes, key=lambda item: item.key)
        priorities = [
            lambda item: item.unlock_milestone,
            lambda item: item.output_key == "food" and state.balances.get("food", 0) < policy.profession_food_target,
            lambda item: item.output_key.startswith("potion") and state.balances.get(item.output_key, 0) < policy.profession_potion_target,
            lambda item: all(state.balances.get(key, 0) >= amount for key, amount in item.required_inputs.items()),
        ]
        recipe = next((item for predicate in priorities for item in recipes if predicate(item)), None)
        if recipe is None:
            return None
        horizon = policy.profession_horizon_hours * 3600
        count = min(capacity, max(1, (max(0, horizon - queued_seconds) + recipe.duration_seconds - 1) // recipe.duration_seconds))
        return ProfessionEnqueueAction(actionKey=recipe.key, count=count)

    @staticmethod
    def _plan(
        locked: LockedAccount,
        family: ActionFamily,
        decisions: Sequence[Decision],
        projection: dict[str, object],
        now: datetime,
        *,
        risk: RiskClass = "low",
    ) -> TypedActionPlan:
        keyed = {"entries": "key", "equipment": "key", "affixes": "key", "instances": "key", "queue": "key", "recipes": "key"}
        return TypedActionPlan(
            account_id=locked.account_id,
            state_fingerprint=canonical_fingerprint(family, projection, keyed_arrays=keyed),
            policy_version=locked.policy.version,
            proposedActions=list(decisions),
            estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
            risk=risk,
            expires_at=now + timedelta(minutes=5),
            confirmation_required=False,
        )
