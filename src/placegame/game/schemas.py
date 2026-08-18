from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LoginRequest(RequestModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ViewSectionsRequest(RequestModel):
    sections: tuple[str, ...]
    section_etags: dict[str, str] | None = Field(default=None, alias="sectionEtags")


class BossPreviewRequest(RequestModel):
    boss_key: str = Field(alias="bossKey", min_length=1)
    difficulty: Literal["normal", "hard", "nightmare"]
    selected_skill_keys: list[str] = Field(alias="selectedSkillKeys", max_length=3)
    buff_key: Literal["none", "assault", "guard", "focus"] = Field(alias="buffKey")
    affix_key: str | None = Field(alias="affixKey")
    target_slot: str = Field(alias="targetSlot", min_length=1)
    use_material_boost: bool = Field(alias="useMaterialBoost")


class BossChallengeRequest(BossPreviewRequest):
    pass


class BossAssistRequest(RequestModel):
    boss_key: str = Field(alias="bossKey", min_length=1)


class ProfessionEnqueueRequest(RequestModel):
    action_key: str = Field(alias="actionKey", min_length=1)
    count: int = Field(ge=1)


class ProfessionSupplyEquipRequest(RequestModel):
    supply_type: str = Field(alias="supplyType", min_length=1)
    item_key: str = Field(alias="itemKey", min_length=1)


class DailyClaimRequest(RequestModel):
    point: int = Field(ge=0)


class QuestClaimRequest(RequestModel):
    quest_key: str = Field(alias="questKey", min_length=1)


class AchievementClaimRequest(RequestModel):
    achievement_key: str = Field(alias="achievementKey", min_length=1)


class CodexClaimRequest(RequestModel):
    reward_key: str = Field(alias="rewardKey", min_length=1)


class MailClaimRequest(RequestModel):
    mail_id: str = Field(alias="mailId", min_length=1)


class ResponseData(BaseModel):
    """The required stable core of an endpoint response."""

    model_config = ConfigDict(
        extra="allow", populate_by_name=True, strict=True
    )


class LoginResult(ResponseData):
    token: str = Field(min_length=1)


class BootstrapState(ResponseData):
    account_id: str = Field(
        alias="accountId", min_length=1, max_length=128, pattern=r"\S"
    )


class Catalog(ResponseData):
    combat_balance_version: str = Field(alias="combatBalanceVersion", min_length=1)


class IdleSummary(ResponseData):
    accumulated_seconds: int = Field(alias="accumulatedSeconds", ge=0)
    capacity_seconds: int = Field(alias="capacitySeconds", gt=0)


class BossDifficultyOption(ResponseData):
    key: str = Field(min_length=1)
    predicted_win: bool = Field(alias="predictedWin")
    chance: float = Field(ge=0, le=100)
    player_hp_remaining_percent: float = Field(
        alias="playerHpRemainingPercent", ge=0, le=100
    )
    boss_hp_remaining_percent: float = Field(
        alias="bossHpRemainingPercent", ge=0, le=100
    )


class PersonalAttemptPool(ResponseData):
    free_remaining: int = Field(alias="freeRemaining", ge=0)
    free_limit: int = Field(alias="freeLimit", ge=0)
    ticket_used: int = Field(alias="ticketUsed", ge=0)
    ticket_limit: int = Field(alias="ticketLimit", ge=0)


class BossSectionEntry(ResponseData):
    key: str = Field(min_length=1)
    type: str = Field(min_length=1)
    attempts: int | None = Field(default=None, ge=0)
    blocked_reason: str | None = Field(alias="blockedReason")
    difficulty_options: list[BossDifficultyOption] = Field(alias="difficultyOptions")
    refresh_text: str | None = Field(default=None, alias="refreshText")
    personal_attempt_pool: PersonalAttemptPool | None = Field(
        default=None, alias="personalAttemptPool"
    )


class ViewSections(ResponseData):
    section_etags: dict[str, str] = Field(alias="sectionEtags")
    bosses: list[BossSectionEntry] | None = None
    boss_state: BossState | None = Field(alias="bossState", default=None)
    world_boss_state: WorldBossState | None = Field(alias="worldBossState", default=None)
    profession_state: ProfessionState | None = Field(alias="professionState", default=None)
    reward_state: RewardState | None = Field(alias="rewardState", default=None)


class IdleCollectResult(ResponseData):
    collected: bool


class BossPreview(ResponseData):
    predicted_win: bool = Field(alias="predictedWin")
    chance: float = Field(ge=0, le=100)
    player_hp_remaining_percent: float = Field(
        alias="playerHpRemainingPercent", ge=0, le=100
    )
    boss_hp_remaining_percent: float = Field(
        alias="bossHpRemainingPercent", ge=0, le=100
    )


class BossChallengeResult(ResponseData):
    won: bool


class BossAssistResult(ResponseData):
    my_attempt_count: int = Field(alias="myAttemptCount", ge=0)
    remaining_attempt_count: int = Field(alias="remainingAttemptCount", ge=0)


class ProfessionSettleResult(ResponseData):
    selected_profession_key: str = Field(alias="selectedProfessionKey", min_length=1)
    queue_size: int = Field(alias="queueSize", ge=0)


class ProfessionQueueResult(ResponseData):
    queue_size: int = Field(alias="queueSize", ge=0)


class ProfessionSupplyResult(ResponseData):
    equipped: bool


class RewardClaimResult(ResponseData):
    claimed: bool


class BossEntryState(ResponseData):
    key: str = Field(min_length=1)
    type: str = Field(min_length=1)
    required_level: int = Field(alias="requiredLevel", ge=0)
    attempts: int | None = Field(alias="attempts", default=None, ge=0)
    blocked_reason: str | None = Field(alias="blockedReason", default=None)
    refresh_key: str | None = Field(alias="refreshKey", default=None)
    difficulty_options: list[Literal["normal", "hard", "nightmare"]] = Field(
        alias="difficultyOptions", min_length=1
    )


class EquipmentSlot(ResponseData):
    key: str = Field(min_length=1)
    score: int = Field(ge=0)
    eligible: bool


class PotionState(ResponseData):
    active_key: str | None = Field(alias="activeKey", default=None)
    required_key: str | None = Field(alias="requiredKey", default=None)


class BossAffix(ResponseData):
    key: str = Field(min_length=1)
    multiplier: float = Field(gt=0)


class BossState(ResponseData):
    entries: list[BossEntryState] = Field(min_length=1)
    free_attempts: int = Field(alias="freeAttempts", ge=0)
    material_balance: int = Field(alias="materialBalance", ge=0)
    equipment: list[EquipmentSlot] = Field(min_length=1)
    potion: PotionState
    affixes: list[BossAffix]


class WorldBossInstance(ResponseData):
    key: str = Field(min_length=1)
    lifecycle: str = Field(min_length=1)
    active: bool
    alive: bool
    my_attempt_count: int = Field(alias="myAttemptCount", ge=0)
    remaining_attempt_count: int = Field(alias="remainingAttemptCount", ge=0)


class WorldBossState(ResponseData):
    instances: list[WorldBossInstance] = Field(min_length=1)


class ProfessionQueueEntry(ResponseData):
    action_key: str = Field(alias="actionKey", min_length=1)
    remaining_seconds: int = Field(alias="remainingSeconds", ge=0)


class ProfessionRecipe(ResponseData):
    key: str = Field(min_length=1)
    duration_seconds: int = Field(alias="durationSeconds", gt=0)
    output_key: str = Field(alias="outputKey", min_length=1)
    output_count: int = Field(alias="outputCount", gt=0)
    required_inputs: dict[str, int] = Field(alias="requiredInputs")
    unlock_milestone: bool = Field(alias="unlockMilestone", default=False)


class ProfessionState(ResponseData):
    selected_profession_key: str = Field(alias="selectedProfessionKey", min_length=1)
    queue: list[ProfessionQueueEntry]
    unlock_progress: int = Field(alias="unlockProgress", ge=0)
    recipes: list[ProfessionRecipe] = Field(min_length=1)
    balances: dict[str, int]
    recipe_version: str = Field(alias="recipeVersion", min_length=1)


class RewardCandidate(ResponseData):
    kind: str = Field(min_length=1)
    identifier: str = Field(min_length=1)
    completed: bool
    claimed: bool
    choice_count: int = Field(alias="choiceCount", ge=0)
    cost: int = Field(ge=0)
    would_overflow: bool = Field(alias="wouldOverflow")


class RewardState(ResponseData):
    inventory_safety_available: bool = Field(alias="inventorySafetyAvailable")
    candidates: list[RewardCandidate]


ViewSections.model_rebuild()
