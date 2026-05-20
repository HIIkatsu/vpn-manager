import asyncio
from app.db.database import async_session_maker
from sqlalchemy import select
from app.db.models import User
from app.services.xray_manager import XrayManager

async def sync():
    x = XrayManager()
    async with async_session_maker() as s:
        users = (await s.execute(select(User).where(User.is_active == True))).scalars().all()
        for u in users:
            await x.add_client(str(u.telegram_id), u.vless_uuid)
            print(f"✅ Влит в Xray: {u.telegram_id}")

if __name__ == "__main__":
    asyncio.run(sync())
