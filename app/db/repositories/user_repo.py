from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import User
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))


    async def get_expiring_in_days(self, days: int) -> list[User]:
        now = datetime.now(timezone.utc)
        start = (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        result = await self.session.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.sub_end_date.is_not(None),
                User.sub_end_date >= start,
                User.sub_end_date < end,
            )
        )
        return list(result.all())
