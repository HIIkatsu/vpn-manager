import asyncio
from app.db.database import async_session_maker
from app.db.models import User
from sqlalchemy import delete

async def clean_test_users():
    async with async_session_maker() as session:
        result = await session.execute(delete(User).where(User.telegram_id >= 9000000))
        await session.commit()
        print(f"🗑️ Удалено тестовых записей: {result.rowcount}")

if __name__ == "__main__":
    asyncio.run(clean_test_users())
