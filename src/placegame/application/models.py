from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class IdleState(_Result):
    accumulated_seconds: int = Field(alias="accumulatedSeconds", ge=0)
    # None whenever the game reports no ceiling, which is currently always.
    capacity_seconds: int | None = Field(default=None, alias="capacitySeconds", gt=0)


class AccountSummary(_Result):
    account_id: UUID
    label: str
    enabled: bool
    paused_reason: str | None
    auth_state: Literal["authenticated", "required", "unknown"]


class AccountStatus(_Result):
    account: AccountSummary
    bootstrap_account_id: str
    idle: IdleState
    fetched_at: datetime


class IdlePreview(_Result):
    account_id: UUID
    plan_id: UUID | None
    decision: Literal["collect", "wait"]
    accumulated_seconds: int = Field(ge=0)
    capacity_seconds: int | None = Field(default=None, gt=0)
    threshold_seconds: int = Field(gt=0)
    expires_at: datetime | None
    reason: str
    correlation_id: str


class IdleExecution(_Result):
    account_id: UUID
    plan_id: UUID
    status: Literal["executed", "reconciled"]
    applied: bool
    reconciled: bool
    collected: bool
    correlation_id: str
