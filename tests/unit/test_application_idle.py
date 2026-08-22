from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from placegame.game.schemas import IdleSummary
from placegame.policy.models import VersionedPolicy


def test_idle_planner_uses_capacity_bound_threshold_and_stable_eligibility_fingerprint():
    from placegame.application.idle import IdlePlanner

    planner = IdlePlanner()
    policy = VersionedPolicy(version=3, idle_threshold_minutes=690)
    first = IdleSummary(validSeconds=700 * 60, capacitySeconds=720 * 60)
    later = IdleSummary(validSeconds=719 * 60, capacitySeconds=720 * 60)

    assert planner.threshold(policy, first) == 690 * 60
    assert planner.decision(first, policy) == "collect"
    assert planner.fingerprint(first, policy) == planner.fingerprint(later, policy)


def test_a_missing_capacity_leaves_the_operator_policy_in_charge():
    """The live game reports no ceiling, so nothing may clamp the policy."""

    from placegame.application.idle import IdlePlanner

    planner = IdlePlanner()
    policy = VersionedPolicy(version=3, idle_threshold_minutes=690)
    # A real account was observed here, past any 8-hour cap we might have assumed.
    observed = IdleSummary(validSeconds=37760.265)

    assert observed.capacity_seconds is None
    assert planner.threshold(policy, observed) == 690 * 60
    # 37760s is under the 41400s policy threshold, so it must still be a wait. A
    # guessed 28800s cap would have wrongly reported this as collectible.
    assert planner.decision(observed, policy) == "wait"


def test_a_server_sent_capacity_still_clamps_the_threshold():
    from placegame.application.idle import IdlePlanner

    policy = VersionedPolicy(version=3, idle_threshold_minutes=690)
    capped = IdleSummary(validSeconds=100, capacitySeconds=60)

    assert IdlePlanner().threshold(policy, capped) == 60


def test_idle_planner_builds_exactly_one_safe_collect_action():
    from placegame.application.idle import IdlePlanner

    account_id = uuid4()
    plan = IdlePlanner().draft(
        account_id,
        IdleSummary(validSeconds=100, capacitySeconds=100),
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
        IdleSummary(validSeconds=1, capacitySeconds=7200),
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
        PreviewAccounts(IdleSummary(validSeconds=7200, capacitySeconds=7200)),
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
        PreviewAccounts(IdleSummary(validSeconds=1, capacitySeconds=7200)),
        store,
    ).preview(uuid4(), actor=Actor("webui", "operator"), correlation_id="preview-2")

    assert result.decision == "wait"
    assert result.plan_id is None
    assert store.drafts == [None]


class _AdvisoryResult:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    def scalar(self):
        return self.acquired


class _AdvisorySession:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.unlock_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, statement, params=None):
        if "try_advisory_lock" in str(statement):
            return _AdvisoryResult(self.acquired)
        self.unlock_called = True
        return _AdvisoryResult(True)

    async def commit(self):
        return None


class _AdvisorySessions:
    def __init__(self, acquired: bool) -> None:
        self.session = _AdvisorySession(acquired)

    def __call__(self):
        return self.session


async def test_idle_execution_guard_reports_busy_account_without_waiting():
    from placegame.application.errors import PlanInProgress
    from placegame.application.idle import IdleExecutionGuard

    sessions = _AdvisorySessions(False)
    with pytest.raises(PlanInProgress, match="plan_in_progress"):
        async with IdleExecutionGuard(sessions).hold(uuid4()):
            raise AssertionError("busy account must not enter the guarded body")
    assert sessions.session.unlock_called is False
