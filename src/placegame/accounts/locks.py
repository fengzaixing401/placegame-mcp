from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def account_lock(session: AsyncSession, account_id: UUID):
    """Hold the account's transaction-scoped advisory lock until commit/rollback."""

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:account_id, 0))"
        ),
        {"account_id": str(account_id)},
    )
    yield


@asynccontextmanager
async def identity_lock(session: AsyncSession, game_account_id: str):
    """Serialize enrollment by external game identity, independent of local locks."""

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:game_account_id, 1))"
        ),
        {"game_account_id": game_account_id},
    )
    yield
