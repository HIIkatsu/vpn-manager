import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def get_or_create(self, telegram_id: int) -> User:
        user = await self.users.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, vless_uuid=str(uuid.uuid4()), is_active=False)
            await self.users.add(user)
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.users.get_by_telegram_id(telegram_id)

    async def get_by_uuid(self, user_uuid: str, session: AsyncSession | None = None) -> User | None:
        db_session = session if session is not None else getattr(self.users, "session", self.users)
        return await db_session.scalar(select(User).where(User.vless_uuid == user_uuid))

