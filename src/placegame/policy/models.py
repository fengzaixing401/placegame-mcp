from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from placegame.errors import AccountError


class AccountPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idle_threshold_minutes: int = Field(default=690, ge=60)
    boss_min_chance: int = Field(default=80, ge=50, le=98)
    personal_paid_attempts: bool = False
    world_collaboration_enabled: bool = True
    world_attempts: Literal[3] = 3
    material_reserve: int = Field(default=64, ge=0)
    profession_food_target: int = Field(default=6, ge=0)
    profession_potion_target: int = Field(default=12, ge=0)
    profession_horizon_hours: int = Field(default=12, ge=1)
    inventory_warning_percent: int = Field(default=85, ge=1, le=99)
    inventory_critical_percent: int = Field(default=95, ge=1, le=100)
    inventory_auto_quality_ceiling: Literal["white", "green", "blue"] = "blue"
    inventory_keep_item_ids: frozenset[str] = frozenset()
    inventory_protected_affixes: frozenset[str] = frozenset()
    warehouse_auto_deposit_types: frozenset[str] = frozenset(
        {"boss_material", "profession_material"}
    )
    safe_reward_claims: bool = True

    @model_validator(mode="after")
    def ordered_inventory_thresholds(self) -> Self:
        if self.inventory_critical_percent < self.inventory_warning_percent:
            raise ValueError("critical inventory threshold must be >= warning threshold")
        return self


class VersionedPolicy(AccountPolicy):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)


class PolicyConflict(AccountError):
    def __init__(self) -> None:
        super().__init__("policy conflict")
