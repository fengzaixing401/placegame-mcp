import json
import re
from collections import UserDict
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from alembic import command
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.contracts import Actor
from placegame.errors import PlanPreconditionFailed
from placegame.models import ActionPlan, GameAccount
from placegame.policy.plans import (
    ActionPlanDraft,
    BossAssistAction,
    BlockedDecision,
    EstimatedCosts,
    IdleCollectAction,
    PostgresPlanStore,
    RegisteredAction,
    SelectedDecision,
    SkippedDecision,
    TypedActionPlan,
    canonical_fingerprint,
    canonical_json,
)


ADMIN = Actor("webui", "admin")


@pytest.fixture
def plan_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def plan_sessions(plan_database_url):
    engine = create_async_engine(plan_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE audit_events, job_runs, jobs, action_plans, "
                "account_snapshots, account_policies, game_accounts RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


def make_plan(actions):
    return TypedActionPlan(
        account_id=uuid4(),
        state_fingerprint=canonical_fingerprint("idle", {"seconds": 1}),
        policy_version=1,
        proposedActions=[
            SelectedDecision(family="idle", reason="eligible", action=action)
            for action in actions
        ],
        estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
        risk="low",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        confirmation_required=False,
    )


def make_idle_plan():
    return make_plan([IdleCollectAction()])


def test_canonical_fingerprint_sorts_object_keys_and_declared_keyed_arrays():
    left = canonical_fingerprint(
        "idle",
        {"items": [{"key": "b"}, {"key": "a"}], "seconds": 1},
        keyed_arrays={"items": "key"},
    )
    right = canonical_fingerprint(
        "idle",
        {"seconds": 1, "items": [{"key": "a"}, {"key": "b"}]},
        keyed_arrays={"items": "key"},
    )
    assert left == right and re.fullmatch(r"pgfp:v1:[0-9a-f]{64}", left)


def test_canonical_fingerprint_preserves_semantic_sequence_order():
    assert canonical_fingerprint("idle", {"steps": ["a", "b"]}) != canonical_fingerprint(
        "idle", {"steps": ["b", "a"]}
    )


@pytest.mark.parametrize(
    "value", [1.5, b"bytes", datetime.now(timezone.utc), float("nan"), object()]
)
def test_canonical_json_rejects_non_json_values(value):
    with pytest.raises(TypeError):
        canonical_json(value)


def test_typed_plan_json_round_trip_uses_aliases():
    plan = make_idle_plan()
    restored = TypedActionPlan.from_json(plan.to_json())
    assert restored == plan
    assert "idleCollect" not in json.dumps(plan.to_json())


def test_typed_plan_rejects_unknown_action_url_body_and_claim_all():
    with pytest.raises(ValidationError):
        TypeAdapter(RegisteredAction).validate_python(
            {"family": "idle", "kind": "claim_all", "url": "/x", "body": {}}
        )


def test_typed_plan_rejects_mixed_action_families():
    with pytest.raises(ValidationError):
        make_plan([IdleCollectAction(), BossAssistAction(bossKey="b")])


@pytest.mark.parametrize(
    "value",
    [
        ("tuple",),
        UserDict({"key": "value"}),
        MappingProxyType({"key": "value"}),
    ],
)
def test_canonical_json_rejects_non_plain_containers(value):
    with pytest.raises(TypeError):
        canonical_json(value)


@pytest.mark.parametrize("reason", ["has space", "punctuation!", "", "Upper", "x" * 129])
def test_decision_reasons_are_stable_tokens(reason):
    with pytest.raises(ValidationError):
        SelectedDecision(family="idle", reason=reason, action=IdleCollectAction())


def test_plan_round_trip_allows_null_actions_with_an_explicit_family():
    plan = TypedActionPlan(
        account_id=uuid4(),
        state_fingerprint=canonical_fingerprint("profession", {"queue": []}),
        policy_version=1,
        proposedActions=[
            BlockedDecision(family="profession", reason="combat_potion_switch"),
            SkippedDecision(family="profession", reason="not_queued"),
        ],
        estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
        risk="low",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert TypedActionPlan.from_json(plan.to_json()) == plan


def test_draft_excludes_caller_controlled_lifecycle_fields():
    assert {"confirmed_at", "confirmed_by", "execution_state", "executed_at", "execution_result"}.isdisjoint(
        ActionPlanDraft.model_fields
    )
    assert "family" in ActionPlanDraft.model_fields


async def _account_id(plan_sessions) -> UUID:
    async with plan_sessions.begin() as session:
        account = GameAccount(label="plan store", auth_mode="token_only")
        session.add(account)
        await session.flush()
        return account.id


def _draft(account_id):
    return ActionPlanDraft(
        account_id=account_id,
        state_fingerprint=canonical_fingerprint("idle", {"seconds": 1}),
        policy_version=1,
        family="idle",
        proposedActions=[
            SelectedDecision(family="idle", reason="eligible", action=IdleCollectAction())
        ],
        estimatedCosts=EstimatedCosts(material=0, attempts=0, currency=0),
        risk="low",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        confirmation_required=True,
    )


async def test_postgres_plan_store_creates_sanitized_pending_row(plan_sessions):
    account_id = await _account_id(plan_sessions)
    draft = _draft(account_id)

    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(draft)

    assert created.execution_state == "pending"
    assert created.confirmed_at is None
    assert created.confirmed_by is None
    assert created.executed_at is None
    assert created.execution_result is None


async def test_postgres_plan_store_rejects_corrupt_jsonb_on_fresh_account_filtered_reload(
    plan_sessions,
):
    account_id = await _account_id(plan_sessions)
    other_account_id = await _account_id(plan_sessions)
    async with plan_sessions.begin() as session:
        store = PostgresPlanStore(session)
        created = await store.create(_draft(account_id))

    async with plan_sessions.begin() as session:
        store = PostgresPlanStore(session)
        with pytest.raises(PlanPreconditionFailed):
            await store.get_for_update(created.id, other_account_id)
        await session.execute(
            text("UPDATE action_plans SET proposed_actions = CAST(:value AS jsonb) WHERE id = :id"),
            {"id": created.id, "value": json.dumps([{"corrupt": True}])},
        )

    async with plan_sessions.begin() as session:
        with pytest.raises(ValidationError):
            await PostgresPlanStore(session).get_for_update(created.id, account_id)


async def test_postgres_plan_store_rejects_corrupt_execution_result_on_fresh_reload(
    plan_sessions,
):
    account_id = await _account_id(plan_sessions)
    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(_draft(account_id))
        await session.execute(
            text("UPDATE action_plans SET execution_result = CAST(:value AS jsonb) WHERE id = :id"),
            {
                "id": created.id,
                "value": json.dumps({"status": "ok", "raw": {"secret": True}}),
            },
        )

    async with plan_sessions.begin() as session:
        with pytest.raises(ValidationError):
            await PostgresPlanStore(session).get_for_update(created.id, account_id)


async def test_postgres_plan_store_confirms_and_guards_runtime_terminal_arguments(plan_sessions):
    account_id = await _account_id(plan_sessions)
    async with plan_sessions.begin() as session:
        store = PostgresPlanStore(session)
        created = await store.create(_draft(account_id))
        confirmed = await store.confirm(created.id, account_id, actor=ADMIN)
        assert confirmed.execution_state == "confirmed"
        assert confirmed.confirmed_by == "webui:admin"
        await store.mark_executing(created.id, "confirmed")
        await store.finish(created.id, "executed", {"status": "ok", "drop": ["raw"]})

        with pytest.raises(PlanPreconditionFailed):
            await store.finish(created.id, "failed", {})
        with pytest.raises(PlanPreconditionFailed):
            await store.mark_executing(created.id, "executed")  # type: ignore[arg-type]
        with pytest.raises(PlanPreconditionFailed):
            await store.finish(created.id, "invalid", {})  # type: ignore[arg-type]

    async with plan_sessions() as session:
        row = await session.get(ActionPlan, created.id)
    assert row is not None
    assert row.execution_state == "executed"
    assert row.execution_result == {"status": "ok"}


async def test_postgres_plan_store_rejects_expired_confirmation(plan_sessions):
    account_id = await _account_id(plan_sessions)
    expired = _draft(account_id).model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(expired)

    async with plan_sessions.begin() as session:
        with pytest.raises(PlanPreconditionFailed):
            await PostgresPlanStore(session).confirm(created.id, account_id, actor=ADMIN)

    async with plan_sessions() as session:
        row = await session.get(ActionPlan, created.id)
    assert row is not None
    assert row.execution_state == "pending"
    assert row.confirmed_at is None
    assert row.confirmed_by is None


async def test_postgres_plan_store_rejects_expired_execution(plan_sessions):
    account_id = await _account_id(plan_sessions)
    expired = _draft(account_id).model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(expired)

    async with plan_sessions.begin() as session:
        with pytest.raises(PlanPreconditionFailed):
            await PostgresPlanStore(session).mark_executing(created.id, "pending")

    async with plan_sessions() as session:
        row = await session.get(ActionPlan, created.id)
    assert row is not None
    assert row.execution_state == "pending"


async def test_postgres_plan_store_requires_confirmation_before_pending_execution(plan_sessions):
    account_id = await _account_id(plan_sessions)
    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(_draft(account_id))

    async with plan_sessions.begin() as session:
        with pytest.raises(PlanPreconditionFailed):
            await PostgresPlanStore(session).mark_executing(created.id, "pending")

    async with plan_sessions() as session:
        row = await session.get(ActionPlan, created.id)
    assert row is not None
    assert row.execution_state == "pending"


async def test_postgres_plan_store_expected_state_mismatch_preserves_row(plan_sessions):
    account_id = await _account_id(plan_sessions)
    async with plan_sessions.begin() as session:
        created = await PostgresPlanStore(session).create(_draft(account_id))

    async with plan_sessions.begin() as session:
        with pytest.raises(PlanPreconditionFailed):
            await PostgresPlanStore(session).mark_executing(created.id, "confirmed")

    async with plan_sessions() as session:
        row = await session.get(ActionPlan, created.id)
    assert row is not None
    assert row.execution_state == "pending"
    assert row.confirmed_at is None
    assert row.executed_at is None
    assert row.execution_result is None
