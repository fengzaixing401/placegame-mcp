from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from placegame.accounts.locks import account_lock
from placegame.accounts.repository import AccountRepository
from placegame.contracts import Actor
from placegame.errors import PolicyUnavailable
from placegame.models import AccountPolicy as AccountPolicyRow
from placegame.models import GameAccount

from .models import AccountPolicy, PolicyConflict, VersionedPolicy
from .ports import ServerIdleCapacityReader


class PostgresPolicyService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        capacity_reader: ServerIdleCapacityReader | Callable[[UUID], int | Awaitable[int]],
        repository: AccountRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.capacity_reader = capacity_reader
        self.repository = repository or AccountRepository()

    async def get(self, account_id: UUID) -> VersionedPolicy:
        try:
            async with self.sessions() as session:
                record = await session.execute(
                    select(GameAccount, AccountPolicyRow)
                    .outerjoin(
                        AccountPolicyRow,
                        AccountPolicyRow.account_id == GameAccount.id,
                    )
                    .where(GameAccount.id == account_id)
                )
                row = record.one_or_none()
        except SQLAlchemyError:
            raise PolicyUnavailable() from None

        if row is None:
            raise PolicyUnavailable() from None
        account, policy_row = row
        return self._versioned(account, policy_row)

    async def save(
        self,
        account_id: UUID,
        policy: AccountPolicy,
        expected_version: int,
        *,
        actor: Actor,
    ) -> VersionedPolicy:
        conflict = False
        unavailable = False
        saved: VersionedPolicy | None = None

        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                account = await session.scalar(
                    select(GameAccount)
                    .where(GameAccount.id == account_id)
                    .with_for_update()
                )
                policy_row = await session.scalar(
                    select(AccountPolicyRow)
                    .where(AccountPolicyRow.account_id == account_id)
                    .with_for_update()
                )
                if account is None:
                    unavailable = True
                else:
                    try:
                        self._versioned(account, policy_row)
                    except PolicyUnavailable:
                        unavailable = True

                if not unavailable and account is not None:
                    if (
                        isinstance(expected_version, bool)
                        or not isinstance(expected_version, int)
                        or expected_version < 1
                        or expected_version != account.policy_version
                        or (
                            policy_row is not None
                            and expected_version != policy_row.policy_version
                        )
                    ):
                        conflict = True
                    else:
                        next_version = expected_version + 1
                        if policy_row is None:
                            policy_row = AccountPolicyRow(
                                account_id=account_id,
                                policy=policy.model_dump(mode="json"),
                                policy_version=next_version,
                            )
                            session.add(policy_row)
                        else:
                            policy_row.policy = policy.model_dump(mode="json")
                            policy_row.policy_version = next_version
                        account.policy_version = next_version
                        await session.flush()
                        saved = VersionedPolicy(
                            version=next_version,
                            **policy.model_dump(),
                        )

                if account is not None and not unavailable and conflict:
                    await self.repository.add_audit(
                        session,
                        actor=f"{actor.kind}:{actor.actor_id}",
                        source=actor.kind,
                        account_id=account_id,
                        action="policy.save",
                        result={"status": "conflict", "error": "PolicyConflict"},
                    )
                elif account is not None and not unavailable and saved is not None:
                    await self.repository.add_audit(
                        session,
                        actor=f"{actor.kind}:{actor.actor_id}",
                        source=actor.kind,
                        account_id=account_id,
                        action="policy.save",
                        result={"status": "saved", "version": saved.version},
                    )

        if unavailable:
            raise PolicyUnavailable() from None
        if conflict:
            raise PolicyConflict() from None
        if saved is None:
            raise PolicyUnavailable() from None
        return saved

    async def server_idle_capacity(self, account_id: UUID) -> int:
        try:
            result = self.capacity_reader(account_id)
            capacity = await result if inspect.isawaitable(result) else result
        except Exception:
            raise PolicyUnavailable() from None
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
            raise PolicyUnavailable() from None
        return capacity

    @staticmethod
    def _versioned(
        account: GameAccount, policy_row: AccountPolicyRow | None
    ) -> VersionedPolicy:
        if account.policy_version < 1:
            raise PolicyUnavailable() from None
        if policy_row is None:
            if account.policy_version != 1:
                raise PolicyUnavailable() from None
            return VersionedPolicy(version=1, **AccountPolicy().model_dump())
        if not isinstance(policy_row.policy, dict):
            raise PolicyUnavailable() from None
        if policy_row.policy_version != account.policy_version:
            raise PolicyUnavailable() from None
        try:
            policy = AccountPolicy.model_validate(policy_row.policy)
            return VersionedPolicy(version=account.policy_version, **policy.model_dump())
        except ValidationError:
            raise PolicyUnavailable() from None
