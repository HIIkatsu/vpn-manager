import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def get_or_create(self, telegram_id: int, username: str = None):
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            import uuid
            from app.db.models import User
            user = User(telegram_id=telegram_id, username=username, vless_uuid=str(uuid.uuid4()), is_active=False)
            db_session = getattr(self.users, "session", self.users)
            db_session.add(user)
            await db_session.commit()
        else:
            if username and getattr(user, 'username', '') != username:
                user.username = username
                db_session = getattr(self.users, "session", self.users)
                await db_session.commit()
        return user

    async def get_by_telegram_id(self, telegram_id: int):
        from sqlalchemy.future import select
        from app.db.models import User
        db_session = getattr(self.users, "session", self.users)
        result = await db_session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalars().first()

    async def get_by_uuid(self, user_uuid: str, session: AsyncSession | None = None) -> User | None:
        db_session = session if session is not None else getattr(self.users, "session", self.users)
        return await db_session.scalar(select(User).where(User.vless_uuid == user_uuid))

