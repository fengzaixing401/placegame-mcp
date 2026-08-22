from datetime import datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from placegame.accounts.service import AccountSnapshot, LockedAccount
from placegame.errors import GameSchemaMismatch
from placegame.game.client import GameApi
from placegame.game.schemas import IdleSummary
from placegame.policy.engine import PolicyEngine
from placegame.policy.models import VersionedPolicy


SHANGHAI = ZoneInfo("Asia/Shanghai")
SHANGHAI_NOW = datetime(2026, 8, 17, 10, 0, tzinfo=SHANGHAI)


class IdleApi:
    def __init__(self) -> None:
        self.idle_summary_result: object = IdleSummary(validSeconds=0, capacitySeconds=720 * 60)
        self.mutation_calls: list[str] = []

    async def idle_summary(self):
        if isinstance(self.idle_summary_result, Exception):
            raise self.idle_summary_result
        return self.idle_summary_result


def locked(api: IdleApi) -> LockedAccount:
    account_id = uuid4()
    return LockedAccount(
        account_id,
        cast(GameApi, api),
        VersionedPolicy(version=1),
        AccountSnapshot(account_id, True, None, True, None, {}, "snapshot", SHANGHAI_NOW, SHANGHAI_NOW),
    )


async def test_idle_threshold_is_minimum_of_policy_and_server_capacity():
    api = IdleApi()
    api.idle_summary_result = IdleSummary(validSeconds=710 * 60, capacitySeconds=720 * 60)
    plan = await PolicyEngine().build_idle_plan(locked(api), now=SHANGHAI_NOW)

    assert plan.family == "idle"
    assert plan.decisions[0].state == "selected"
    assert plan.decisions[0].action.kind == "idle_collect"


def test_world_window_boundaries():
    engine = PolicyEngine()
    assert engine.world_window(datetime(2026, 8, 17, 10, 0, tzinfo=SHANGHAI))
    assert not engine.world_window(datetime(2026, 8, 17, 11, 0, tzinfo=SHANGHAI))


async def test_typed_schema_mismatch_propagates_before_mutation():
    api = IdleApi()
    api.idle_summary_result = GameSchemaMismatch("idle_summary", {"status_code": 200})
    with pytest.raises(GameSchemaMismatch):
        await PolicyEngine().build_idle_plan(locked(api), now=SHANGHAI_NOW)
    assert api.mutation_calls == []
