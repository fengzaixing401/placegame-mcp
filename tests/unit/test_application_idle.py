from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from placegame.game.schemas import IdleSummary
from placegame.policy.models import VersionedPolicy


def test_idle_planner_uses_capacity_bound_threshold_and_stable_eligibility_fingerprint():
    from placegame.application.idle import IdlePlanner

    planner = IdlePlanner()
    policy = VersionedPolicy(version=3, idle_threshold_minutes=690)
    first = IdleSummary(accumulatedSeconds=700 * 60, capacitySeconds=720 * 60)
    later = IdleSummary(accumulatedSeconds=719 * 60, capacitySeconds=720 * 60)

    assert planner.threshold(policy, first) == 690 * 60
    assert planner.decision(first, policy) == "collect"
    assert planner.fingerprint(first, policy) == planner.fingerprint(later, policy)


def test_idle_planner_builds_exactly_one_safe_collect_action():
    from placegame.application.idle import IdlePlanner

    account_id = uuid4()
    plan = IdlePlanner().draft(
        account_id,
        IdleSummary(accumulatedSeconds=100, capacitySeconds=100),
        VersionedPolicy(version=1),
        now=datetime.now(timezone.utc),
    )

    assert plan is not None
    assert plan.risk == "low"
    assert plan.confirmation_required is False
    assert len(plan.decisions) == 1
    assert plan.decisions[0].action.kind == "idle_collect"


def test_idle_planner_wait_does_not_create_a_plan():
    from placegame.application.idle import IdlePlanner

    assert IdlePlanner().draft(
        uuid4(),
        IdleSummary(accumulatedSeconds=1, capacitySeconds=7200),
        VersionedPolicy(version=1),
        now=datetime.now(timezone.utc),
    ) is None


class PreviewAccounts:
    def __init__(self, idle: IdleSummary) -> None:
        self.idle = idle

    @asynccontextmanager
    async def locked(self, account_id, *, actor):
        yield SimpleNamespace(
            account_id=account_id,
            api=SimpleNamespace(idle_summary=self._idle_summary),
            policy=VersionedPolicy(version=2),
        )

    async def _idle_summary(self):
        return self.idle


class PreviewStore:
    def __init__(self) -> None:
        self.drafts = []

    async def save(self, draft, *, actor, correlation_id, preview):
        self.drafts.append(draft)
        return uuid4() if draft is not None else None


async def test_idle_plan_use_case_stores_only_collect_preview_plan():
    from placegame.application.idle import IdlePlanUseCase
    from placegame.contracts import Actor

    account_id = uuid4()
    store = PreviewStore()
    result = await IdlePlanUseCase(
        PreviewAccounts(IdleSummary(accumulatedSeconds=7200, capacitySeconds=7200)),
        store,
        clock=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
    ).preview(account_id, actor=Actor("webui", "operator"), correlation_id="preview-1")

    assert result.decision == "collect"
    assert result.plan_id is not None
    assert result.expires_at == datetime(2026, 8, 19, 0, 5, tzinfo=timezone.utc)
    assert len(store.drafts) == 1


async def test_idle_plan_use_case_records_wait_without_creating_plan():
    from placegame.application.idle import IdlePlanUseCase
    from placegame.contracts import Actor

    store = PreviewStore()
    result = await IdlePlanUseCase(
        PreviewAccounts(IdleSummary(accumulatedSeconds=1, capacitySeconds=7200)),
        store,
    ).preview(uuid4(), actor=Actor("webui", "operator"), correlation_id="preview-2")

    assert result.decision == "wait"
    assert result.plan_id is None
    assert store.drafts == [None]
