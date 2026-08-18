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
