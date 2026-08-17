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
    """A validated JSON object while endpoint-specific fields evolve independently."""

    model_config = ConfigDict(extra="allow")


class LoginResult(ResponseData):
    pass


class BootstrapState(ResponseData):
    pass


class Catalog(ResponseData):
    pass


class IdleSummary(ResponseData):
    pass


class ViewSections(ResponseData):
    pass


class IdleCollectResult(ResponseData):
    pass


class BossPreview(ResponseData):
    pass


class BossChallengeResult(ResponseData):
    pass


class BossAssistResult(ResponseData):
    pass


class ProfessionSettleResult(ResponseData):
    pass


class ProfessionQueueResult(ResponseData):
    pass


class ProfessionSupplyResult(ResponseData):
    pass


class RewardClaimResult(ResponseData):
    pass
