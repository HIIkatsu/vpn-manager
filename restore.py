import asyncio
from datetime import datetime, timedelta, timezone
from app.db.database import async_session_maker
from app.db.models import User
from sqlalchemy import select

users_data = [
    ("1632881013", "f67a8345-3e47-4ddf-a568-6781631f5873"),
    ("1780466329", "b489d545-04a6-4d90-8afd-9e11b72e4c41"),
    ("1480942640", "994e61ba-d102-45d2-a20c-4abb816de65d"),
    ("840649258", "98b912a13fe74fe78fdcbaade1671a2e"),
    ("1778580693", "09dcb601-db2c-45bf-b044-b11db9f98279"),
    ("1078937205", "81d04058-8c4c-43d7-a347-f4b637c33a2a"),
    ("1445959170", "4e099418-e505-41e6-b206-ab7d492500b5")
]

async def restore():
    async with async_session_maker() as session:
        for tg_id, uuid in users_data:
            result = await session.execute(select(User).where(User.telegram_id == int(tg_id)))
            existing = result.scalars().first()
            if not existing:
                new_user = User(
                    telegram_id=int(tg_id),
                    username=f"restored_{tg_id}",
                    vless_uuid=uuid,
                    is_active=True,
                    sub_end_date=datetime.now(timezone.utc) + timedelta(days=30),
                    preferred_os="android"
                )
                session.add(new_user)
        await session.commit()
        print("✅ 7 пользователей успешно восстановлены в БД!")

if __name__ == "__main__":
    asyncio.run(restore())
