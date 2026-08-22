from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from placegame.game.client import GameApi
from placegame.game.schemas import BossEntryState, BossState, EquipmentSlot
from placegame.game.schemas import BossPreview as Preview
from placegame.game.schemas import BossPreviewRequest
from placegame.policy.models import VersionedPolicy
from placegame.policy.plans import BlockedDecision, BossChallengeAction, SelectedDecision


@dataclass(frozen=True)
class BossSelection:
    boss_key: str | None
    difficulty: Literal["normal", "hard", "nightmare"] | None
    preview: Preview | None
    action: BossChallengeAction | None
    decision: SelectedDecision | BlockedDecision


@dataclass(frozen=True)
class _Candidate:
    request: BossPreviewRequest
    preview: Preview

    @property
    def tie_key(self) -> tuple[str, str, str]:
        return (
            ",".join(self.request.selected_skill_keys),
            self.request.buff_key,
            self.request.affix_key or "",
        )


class BossOptimizer:
    def __init__(self, api: GameApi) -> None:
        self.api = api

    async def optimize(self, state: BossState, policy: VersionedPolicy) -> BossSelection:
        if state.potion.required_key not in (None, state.potion.active_key):
            return BossSelection(
                None,
                None,
                None,
                None,
                BlockedDecision(
                    family="personal_boss",
                    reason="combat_potion_switch_required",
                ),
            )
        slot = self._target_slot(state.equipment)
        if slot is None:
            return BossSelection(
                None,
                None,
                None,
                None,
                BlockedDecision(family="personal_boss", reason="no_eligible_equipment"),
            )
        entries = sorted(state.entries, key=lambda entry: (-entry.required_level, entry.key))
        for entry in entries:
            if entry.blocked_reason is not None:
                continue
            for difficulty in ("nightmare", "hard", "normal"):
                if difficulty not in entry.difficulty_options:
                    continue
                selection = await self._for_entry(entry, difficulty, slot, state, policy)
                if selection is not None:
                    return selection
        return BossSelection(
            None,
            None,
            None,
            None,
            BlockedDecision(family="personal_boss", reason="no_eligible_boss"),
        )

    async def _for_entry(
        self,
        entry: BossEntryState,
        difficulty: Literal["normal", "hard", "nightmare"],
        slot: EquipmentSlot,
        state: BossState,
        policy: VersionedPolicy,
    ) -> BossSelection | None:
        baseline: list[_Candidate] = []
        for skill in ("output", "survival", "balanced"):
            for buff in ("none", "assault", "guard", "focus"):
                request = BossPreviewRequest(
                    bossKey=entry.key,
                    difficulty=difficulty,
                    selectedSkillKeys=[skill],
                    buffKey=buff,
                    affixKey="none",
                    targetSlot=slot.key,
                    useMaterialBoost=False,
                )
                baseline.append(_Candidate(request, await self.api.boss_preview(request)))
        shortlist = sorted(
            baseline,
            key=lambda item: (
                -int(item.preview.predicted_win),
                -item.preview.chance,
                -item.preview.player_hp_remaining_percent,
                item.preview.boss_hp_remaining_percent,
                item.tie_key,
            ),
        )[:3]
        eligible = [item for item in shortlist if self._eligible(item.preview, policy)]
        # An active potion is retained, never equipped. Easy combat avoids it by
        # retaining the same ordinary preview configuration.
        final = eligible[0] if eligible else None
        affixes = sorted(state.affixes, key=lambda item: (-item.multiplier, item.key))[:12]
        previewed_affixes = 0
        for affix in affixes:
            for candidate in shortlist:
                if previewed_affixes >= 12:
                    break
                request = candidate.request.model_copy(update={"affix_key": affix.key})
                preview = await self.api.boss_preview(request)
                previewed_affixes += 1
                proposed = _Candidate(request, preview)
                if self._eligible(preview, policy):
                    final = proposed
                    break
            if final is not None and final.request.affix_key == affix.key:
                break
        if final is None:
            return None
        use_material = difficulty in {"hard", "nightmare"} and state.material_balance > policy.material_reserve
        action = BossChallengeAction(
            family="personal_boss",
            bossKey=entry.key,
            difficulty=difficulty,
            selectedSkillKeys=final.request.selected_skill_keys,
            buffKey=final.request.buff_key,
            affixKey=final.request.affix_key,
            targetSlot=slot.key,
            useMaterialBoost=use_material,
        )
        return BossSelection(
            entry.key,
            difficulty,
            final.preview,
            action,
            SelectedDecision(family="personal_boss", reason="boss_eligible", action=action),
        )

    @staticmethod
    def _eligible(preview: Preview, policy: VersionedPolicy) -> bool:
        return preview.predicted_win and preview.chance >= policy.boss_min_chance

    @staticmethod
    def _target_slot(slots: list[EquipmentSlot]) -> EquipmentSlot | None:
        eligible = [slot for slot in slots if slot.eligible]
        return min(eligible, key=lambda slot: (slot.score, slot.key), default=None)
