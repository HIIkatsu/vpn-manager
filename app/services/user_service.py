import uuid

from app.db.models import User
from app.db.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    async def get_or_create(self, telegram_id: int) -> User:
        user = await self.users.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, vless_uuid=uuid.uuid4().hex, is_active=False)
            await self.users.add(user)
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.users.get_by_telegram_id(telegram_id)
