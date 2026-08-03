import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import User
from app.db.repositories.user_repo import UserRepository

class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def get_or_create(self, telegram_id: int, username: str = None, referrer_telegram_id: int = None):
        user = await self.get_by_telegram_id(telegram_id)
        is_new = False
        if user is None:
            is_new = True
            from datetime import datetime, timedelta, timezone
            user = User(
                telegram_id=telegram_id,
                username=username,
                vless_uuid=str(uuid.uuid4()),
                is_active=True,
                sub_end_date=datetime.now(timezone.utc) + timedelta(days=3),
                preferred_os="android",
                referrer_telegram_id=referrer_telegram_id
            )
            db_session = getattr(self.users, "session", self.users)
            db_session.add(user)
            await db_session.flush()
            
            import json, time
            from app.db.repositories.outbox_repo import OutboxRepository
            outbox = OutboxRepository(db_session)
            await outbox.enqueue(
                event_type="xray.add_client",
                aggregate_type="user",
                aggregate_id=str(user.id),
                dedup_key=f"xray.add_client:{user.id}:trial_{int(time.time())}",
                payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid})
            )
        else:
            if username and getattr(user, 'username', '') != username:
                user.username = username
                db_session = getattr(self.users, "session", self.users)
                await db_session.flush()
        return user, is_new

    async def set_preferred_os(self, telegram_id: int, preferred_os: str) -> User | None:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return None
        user.preferred_os = preferred_os
        db_session = getattr(self.users, "session", self.users)
        await db_session.flush()
        return user

    async def get_by_telegram_id(self, telegram_id: int):
        db_session = getattr(self.users, "session", self.users)
        result = await db_session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalars().first()

    async def get_by_uuid(self, user_uuid: str, session: AsyncSession | None = None) -> User | None:
        db_session = session if session is not None else getattr(self.users, "session", self.users)
        return await db_session.scalar(select(User).where(User.vless_uuid == user_uuid))
