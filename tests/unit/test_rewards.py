from placegame.game.schemas import RewardCandidate, RewardState
from placegame.policy.models import VersionedPolicy
from placegame.rewards import SafeRewardPlanner


def test_safe_rewards_skip_choice_cost_overflow_and_unknown():
    state = RewardState(
        inventorySafetyAvailable=True,
        candidates=[
            RewardCandidate(kind="quest", identifier="choice", completed=True, claimed=False, choiceCount=2, cost=0, wouldOverflow=False),
            RewardCandidate(kind="quest", identifier="cost", completed=True, claimed=False, choiceCount=0, cost=1, wouldOverflow=False),
            RewardCandidate(kind="quest", identifier="overflow", completed=True, claimed=False, choiceCount=0, cost=0, wouldOverflow=True),
            RewardCandidate(kind="unknown", identifier="unknown", completed=True, claimed=False, choiceCount=0, cost=0, wouldOverflow=False),
        ],
    )

    plan = SafeRewardPlanner().build(state, VersionedPolicy(version=1))

    assert all(
        decision.state != "selected"
        for decision in plan.decisions
        if decision.action is not None and decision.action.kind != "quest_claim"
    )
    assert {decision.reason for decision in plan.decisions} >= {
        "reward_choice_required",
        "reward_has_cost",
        "reward_inventory_overflow",
        "reward_unknown_kind",
    }
