from types import SimpleNamespace
from typing import cast

import pytest

from placegame.boss_optimizer import BossOptimizer
from placegame.game.client import GameApi
from placegame.game.schemas import BossEntryState, BossState, EquipmentSlot, PotionState
from placegame.policy.models import VersionedPolicy


class PreviewApi:
    def __init__(self) -> None:
        self.preview_count = 0
        self.profession_supply_equip_calls: list[object] = []

    async def boss_preview(self, request):
        self.preview_count += 1
        return SimpleNamespace(
            predicted_win=request.difficulty == "nightmare",
            chance=90.0 if request.difficulty == "nightmare" else 80.0,
            player_hp_remaining_percent=75.0,
            boss_hp_remaining_percent=0.0,
        )


def personal_boss_state(*, active_potion: str = "guard", required_potion: str | None = None) -> BossState:
    return BossState(
        entries=[BossEntryState(key="personal", type="personal", requiredLevel=50, attempts=None, blockedReason=None, refreshKey="daily", difficultyOptions=["normal", "hard", "nightmare"])],
        freeAttempts=1,
        materialBalance=100,
        equipment=[EquipmentSlot(key="weapon", score=1, eligible=True)],
        potion=PotionState(activeKey=active_potion, requiredKey=required_potion),
        affixes=[],
    )


async def test_personal_optimizer_is_bounded_and_prefers_nightmare():
    api = PreviewApi()
    selection = await BossOptimizer(cast(GameApi, api)).optimize(
        personal_boss_state(), VersionedPolicy(version=1)
    )

    assert api.preview_count <= 24
    assert selection.difficulty == "nightmare"
    assert selection.preview is not None
    assert selection.preview.predicted_win
    assert selection.preview.chance >= 80


async def test_combat_potion_switch_is_blocked():
    api = PreviewApi()
    selection = await BossOptimizer(cast(GameApi, api)).optimize(
        personal_boss_state(active_potion="haste", required_potion="guard"),
        VersionedPolicy(version=1),
    )

    assert selection.decision.state == "blocked"
    assert selection.decision.reason == "combat_potion_switch_required"
    assert api.profession_supply_equip_calls == []
