from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from placegame.config import Settings


def get_session(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.read_database_url(), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
