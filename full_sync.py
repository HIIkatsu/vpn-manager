import asyncio
from sqlalchemy.future import select
from app.db.database import async_session_maker
from app.db.models import User
from app.services.node_sync import ActivePushDispatcher

async def main():
    dispatcher = ActivePushDispatcher()
    async with async_session_maker() as session:
        users = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    for u in users:
        print(f"Syncing {u.telegram_id}")
        await dispatcher.add_client(telegram_id=u.telegram_id, uuid=u.vless_uuid)
    print("Done")

asyncio.run(main())
