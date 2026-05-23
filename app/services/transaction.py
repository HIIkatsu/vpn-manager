from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@asynccontextmanager
async def session_scope(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
#             await session.commit() # FIXED: UoW violation
        except Exception:
#             await session.rollback() # FIXED: UoW violation
            raise
