import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from app.db.database import async_session_maker
from app.db.models import User

async def inject_users():
    async with async_session_maker() as session:
        for i in range(50):
            fake_user = User(
                telegram_id=9000000 + i,
                username=f"stress_tester_{i}",
                vless_uuid=str(uuid.uuid4()),
                is_active=True,
                sub_end_date=datetime.now(timezone.utc) + timedelta(days=30),
                preferred_os="android"
            )
            session.add(fake_user)
        
        await session.commit()
        print("✅ 50 фейковых юзеров залиты в БД.")

if __name__ == "__main__":
    asyncio.run(inject_users())
