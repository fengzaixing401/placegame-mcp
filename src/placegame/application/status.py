from collections.abc import Sequence
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from placegame.accounts.service import AccountSnapshot, ManagedAccount
from placegame.contracts import Actor

from .errors import GameContractChanged
from .models import AccountStatus, AccountSummary, IdleState


class _SnapshotState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    account_id: str = Field(alias="accountId", min_length=1, max_length=128)
    idle: IdleState


class AccountServicePort(Protocol):
    async def get(self, account_id: UUID) -> ManagedAccount: ...
    async def snapshot(self, account_id: UUID, *, actor: Actor) -> AccountSnapshot: ...
    async def list_accounts(self) -> Sequence[ManagedAccount]: ...


class AccountStatusQuery:
    def __init__(self, accounts: AccountServicePort) -> None:
        self.accounts = accounts

    async def list(self) -> tuple[AccountSummary, ...]:
        return tuple(self._summary(account) for account in await self.accounts.list_accounts())

    async def get(self, account_id: UUID, *, actor: Actor) -> AccountStatus:
        snapshot = await self.accounts.snapshot(account_id, actor=actor)
        try:
            state = _SnapshotState.model_validate(snapshot.state)
        except ValidationError:
            raise GameContractChanged() from None
        account = await self.accounts.get(account_id)
        return AccountStatus(
            account=AccountSummary(
                account_id=account.id,
                label=account.label,
                enabled=account.enabled,
                paused_reason=account.paused_reason,
                auth_state="authenticated" if snapshot.authenticated else self._listed_auth_state(account),
            ),
            bootstrap_account_id=state.account_id,
            idle=state.idle,
            fetched_at=snapshot.fetched_at,
        )

    @classmethod
    def _summary(cls, account: ManagedAccount) -> AccountSummary:
        return AccountSummary(
            account_id=account.id,
            label=account.label,
            enabled=account.enabled,
            paused_reason=account.paused_reason,
            auth_state=cls._listed_auth_state(account),
        )

    @staticmethod
    def _listed_auth_state(account: ManagedAccount) -> Literal["required", "unknown"]:
        return "required" if account.paused_reason == "authentication_required" else "unknown"
