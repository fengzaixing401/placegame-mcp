from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class RequestModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

class LoginRequest(RequestModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ViewSectionsRequest(RequestModel):
    sections: tuple[str, ...]
    section_etags: dict[str, str] | None = Field(default=None, alias="sectionEtags")


class BossPreviewRequest(RequestModel):
    """Mirrors the official CLI's bossFields(): only bossKey is required.

    Difficulty and buff keys are not enumerated here because the live set comes
    from `view_sections`' `difficultyOptions`; a local allow-list would reject
    values the game adds. `affixKey` defaults to the string "none", not null.
    """

    boss_key: str = Field(alias="bossKey", min_length=1)
    difficulty: str = "normal"
    selected_skill_keys: list[str] = Field(
        default_factory=list, alias="selectedSkillKeys"
    )
    buff_key: str = Field(default="none", alias="buffKey")
    affix_key: str = Field(default="none", alias="affixKey")
    target_slot: str | None = Field(default=None, alias="targetSlot")
    use_material_boost: bool = Field(default=False, alias="useMaterialBoost")


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

    # Most models validate against `data`. Set on subclasses whose fields live at
    # envelope level instead, because identity is returned outside `data`.
    reads_envelope: ClassVar[bool] = False


class EnvelopeResponse(ResponseData):
    reads_envelope: ClassVar[bool] = True


class GameUser(ResponseData):
    id: str = Field(min_length=1, max_length=128, pattern=r"\S")


class LoginResult(ResponseData):
    session_token: str = Field(alias="sessionToken", min_length=1)
    expires_at: int | None = Field(default=None, alias="expiresAt")


class BootstrapState(EnvelopeResponse):
    """The account identity is the envelope-level `user`, not a field in `data`."""

    user: GameUser

    @property
    def account_id(self) -> str:
        return self.user.id


class Catalog(ResponseData):
    # The live catalog carries qualities/jobs/items. It has no version field, so
    # there is nothing stable to require beyond a well-formed object.
    pass


class IdleSummary(ResponseData):
    # The live field is `validSeconds`, a float. The name is the server's own: it
    # reports seconds that already count as collectible.
    valid_seconds: float = Field(alias="validSeconds", ge=0)
    # The live response carries no capacity, and a live account was observed at
    # 37760 valid seconds — past any 8-hour ceiling — so there is no client-side
    # cap to assume. Stays None unless the server starts sending one.
    capacity_seconds: int | None = Field(
        default=None, alias="capacitySeconds", gt=0
    )

    @property
    def accumulated_seconds(self) -> int:
        """Whole seconds, so fingerprints and audit payloads stay integral."""

        return int(self.valid_seconds)


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
    """A collection answers with {adventure, rewardPreview, profile}.

    Observed on 2026-08-22 from a live capture; there is no `collected` flag. The
    payload is passed through rather than modelled, because nothing here is a
    control-flow input and a wrong required field would fail a real collection.
    """


class EquipmentListRequest(RequestModel):
    """`/api/equipment/list` is a GET and takes no parameters."""


class EquipmentIdRequest(RequestModel):
    equipment_id: str = Field(alias="equipmentId", min_length=1, max_length=128)


class EquipmentIdsRequest(RequestModel):
    equipment_ids: list[str] = Field(alias="equipmentIds", min_length=1, max_length=200)


class PassthroughResult(RootModel[Any]):
    """An unmodelled payload of any JSON shape.

    Used where nothing in the response feeds a decision this service makes. A
    root model is deliberate: some of these endpoints answer with a bare array,
    which no field-bearing model can hold. The live shapes are unverified, and a
    required field guessed wrong fails the whole call, so requiring nothing is
    the safe choice.
    """

    model_config = ConfigDict(strict=True)


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
