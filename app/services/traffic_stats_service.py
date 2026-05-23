from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class TrafficStatsService:
    @staticmethod
    async def persist_and_get_total(session: AsyncSession, telegram_id: int, consumed_bytes: int) -> int:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return max(int(consumed_bytes), 0)

        increment = max(int(consumed_bytes), 0)
        user.traffic_total_bytes = max(int(user.traffic_total_bytes), 0) + increment
        user.traffic_last_live_bytes = 0
        await session.flush()
        return user.traffic_total_bytes
