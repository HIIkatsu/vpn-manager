
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.settings import settings

async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.connect() as conn:
        await conn.execute(text("UPDATE outbox_events SET status = 'pending', attempts = 0 WHERE event_type = 'xray.update_client' AND status = 'failed'"))
        await conn.commit()
    await engine.dispose()

asyncio.run(main())
