from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from placegame.game.schemas import RewardCandidate, RewardState
from placegame.policy.models import VersionedPolicy
from placegame.policy.plans import (
    AchievementClaimAction,
    BlockedDecision,
    CodexClaimAction,
    DailyClaimAction,
    EstimatedCosts,
    MailClaimAction,
    QuestClaimAction,
    SelectedDecision,
    SkippedDecision,
    TypedActionPlan,
    canonical_fingerprint,
)


class SafeRewardPlanner:
    def build(
        self,
        state: RewardState,
        policy: VersionedPolicy,
        *,
        account_id: UUID | None = None,
        now: datetime | None = None,
    ) -> TypedActionPlan:
        decisions = []
        selected = False
        for candidate in state.candidates:
            decision = self._decision(candidate, state, policy, selected)
            decisions.append(decision)
            selected = selected or decision.state == "selected"
        if not decisions:
            decisions.append(SkippedDecision(family="safe_reward", reason="no_reward_candidates"))
        projection = {
            "candidates": [
                {
                    "key": candidate.identifier,
                    "kind": candidate.kind,
                    "completed": candidate.completed,
                    "claimed": candidate.claimed,
                    "choiceCount": candidate.choice_count,
                    "cost": candidate.cost,
                    "wouldOverflow": candidate.would_overflow,
                }
                for candidate in state.candidates
            ],
            "inventorySafetyAvailable": state.inventory_safety_available,
        }
        instant = now or datetime.now(timezone.utc)
        return TypedActionPlan(
            account_id=account_id or uuid4(),
            state_fingerprint=canonical_fingerprint(
                "safe_reward", projection, keyed_arrays={"candidates": "key"}
            ),
            policy_version=policy.version,
            proposedActions=decisions,
            estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
            risk="low",
            expires_at=instant + timedelta(minutes=5),
            confirmation_required=False,
        )

    def _decision(
        self,
        candidate: RewardCandidate,
        state: RewardState,
        policy: VersionedPolicy,
        selected: bool,
    ):
        if not policy.safe_reward_claims:
            return SkippedDecision(family="safe_reward", reason="reward_claims_disabled")
        if not state.inventory_safety_available:
            return BlockedDecision(family="safe_reward", reason="inventory_safety_unavailable")
        if not candidate.completed:
            return SkippedDecision(family="safe_reward", reason="reward_incomplete")
        if candidate.claimed:
            return SkippedDecision(family="safe_reward", reason="reward_already_claimed")
        if candidate.choice_count:
            return SkippedDecision(family="safe_reward", reason="reward_choice_required")
        if candidate.cost:
            return SkippedDecision(family="safe_reward", reason="reward_has_cost")
        if candidate.would_overflow:
            return SkippedDecision(family="safe_reward", reason="reward_inventory_overflow")
        action = self._action(candidate)
        if action is None:
            return SkippedDecision(family="safe_reward", reason="reward_unknown_kind")
        if selected:
            return SkippedDecision(family="safe_reward", reason="reward_plan_limit")
        return SelectedDecision(family="safe_reward", reason="reward_safe_to_claim", action=action)

    @staticmethod
    def _action(candidate: RewardCandidate):
        if candidate.kind == "daily":
            try:
                return DailyClaimAction(point=int(candidate.identifier))
            except ValueError:
                return None
        if candidate.kind == "quest":
            return QuestClaimAction(questKey=candidate.identifier)
        if candidate.kind == "achievement":
            return AchievementClaimAction(achievementKey=candidate.identifier)
        if candidate.kind == "codex":
            return CodexClaimAction(rewardKey=candidate.identifier)
        if candidate.kind == "mail":
            return MailClaimAction(mailId=candidate.identifier)
        return None
