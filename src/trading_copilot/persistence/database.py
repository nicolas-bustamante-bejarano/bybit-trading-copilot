from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.config import settings

engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with session_factory() as session:
        yield session
