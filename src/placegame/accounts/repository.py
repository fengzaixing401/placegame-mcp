from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from placegame.models import AccountSnapshot as AccountSnapshotRecord
from placegame.models import AuditEvent, GameAccount, Job


class AccountRepository:
    async def get(self, session: AsyncSession, account_id: UUID) -> GameAccount | None:
        return await session.get(GameAccount, account_id)

    async def get_for_update(
        self, session: AsyncSession, account_id: UUID
    ) -> GameAccount | None:
        return await session.scalar(
            select(GameAccount)
            .where(GameAccount.id == account_id)
            .with_for_update()
        )

    async def has_unresolved_identity(self, session: AsyncSession) -> bool:
        return bool(
            await session.scalar(
                select(GameAccount.id)
                .where(GameAccount.game_account_id.is_(None))
                .limit(1)
            )
        )

    async def add_audit(
        self,
        session: AsyncSession,
        *,
        actor: str,
        source: str,
        account_id: UUID | None,
        action: str,
        result: Mapping[str, Any] | None = None,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        plan_id: UUID | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                actor=actor,
                source=source,
                account_id=account_id,
                plan_id=plan_id,
                action=action,
                result=dict(result) if result is not None else None,
                before=dict(before) if before is not None else None,
                after=dict(after) if after is not None else None,
            )
        )

    async def disable_jobs(self, session: AsyncSession, account_id: UUID) -> int:
        jobs = (
            await session.scalars(
                select(Job).where(Job.account_id == account_id, Job.enabled.is_(True))
            )
        ).all()
        for job in jobs:
            job.enabled = False
        return len(jobs)

    async def save_snapshot(
        self,
        session: AsyncSession,
        *,
        account_id: UUID,
        state: Mapping[str, Any],
        state_fingerprint: str,
        fetched_at: datetime,
        expires_at: datetime,
    ) -> AccountSnapshotRecord:
        record = AccountSnapshotRecord(
            account_id=account_id,
            sanitized_state=dict(state),
            state_fingerprint=state_fingerprint,
            fetched_at=fetched_at,
            expires_at=expires_at,
        )
        session.add(record)
        await session.flush()
        return record
