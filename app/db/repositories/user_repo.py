from sqlalchemy import select

from app.db.models import User
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))
