from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True, connect_args={"prepared_statement_cache_size": 0})

async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
