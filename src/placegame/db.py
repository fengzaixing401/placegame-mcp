from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from placegame.config import Settings


@dataclass
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    _closed: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        engine = create_async_engine(settings.read_database_url(), pool_pre_ping=True)
        return cls(engine=engine, sessions=async_sessionmaker(engine, expire_on_commit=False))

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self.engine.dispose()


def get_session(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Compatibility helper for pre-P1 callers."""
    return Database.from_settings(settings).sessions
