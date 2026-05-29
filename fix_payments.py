import asyncio
from app.api.dependencies.common import get_async_session
from sqlalchemy import update
from app.db.models import Payment

async def fix():
    gen = get_async_session()
    session = await anext(gen)
    await session.execute(update(Payment).where(Payment.status == "pending").values(status="canceled"))
    await session.commit()
    print("✅ Старые зависшие платежи успешно отменены!")

asyncio.run(fix())
