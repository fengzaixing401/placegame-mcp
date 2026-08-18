import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from placegame.policy.plans import (
    BossAssistAction,
    EstimatedCosts,
    IdleCollectAction,
    RegisteredAction,
    SelectedDecision,
    TypedActionPlan,
    canonical_fingerprint,
    canonical_json,
)


def make_plan(actions):
    return TypedActionPlan(
        account_id=uuid4(),
        state_fingerprint=canonical_fingerprint("idle", {"seconds": 1}),
        policy_version=1,
        proposedActions=[
            SelectedDecision(reason="eligible", action=action) for action in actions
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
