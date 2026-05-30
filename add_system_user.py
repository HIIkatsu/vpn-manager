import asyncio
from datetime import datetime, timezone, timedelta
from app.db.database import async_session_maker
from app.db.models import User
from sqlalchemy import select

async def add_system_user():
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.telegram_id == 0))
        if not result.scalars().first():
            system_user = User(
                telegram_id=0,
                username="system_transit_node",
                vless_uuid="11111111-1111-1111-1111-111111111111",
                is_active=True,
                sub_end_date=datetime.now(timezone.utc) + timedelta(days=3650),
                preferred_os="android"
            )
            session.add(system_user)
            await session.commit()
            print("✅ Системный транзитный UUID зафиксирован в базе.")
        else:
            print("ℹ️ Системный UUID уже в базе.")

if __name__ == "__main__":
    asyncio.run(add_system_user())
