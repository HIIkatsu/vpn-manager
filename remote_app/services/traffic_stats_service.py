from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class TrafficStatsService:
    @staticmethod
    async def get_total_with_live(session: AsyncSession, telegram_id: int, live_bytes: int = 0) -> int:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        base_total = max(int(user.traffic_total_bytes or 0), 0) if user is not None else 0
        return base_total + max(int(live_bytes or 0), 0)

    @staticmethod
    async def persist_and_get_total(session: AsyncSession, telegram_id: int, consumed_bytes: int) -> int:
        increment = max(int(consumed_bytes), 0)
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(
                traffic_total_bytes=User.traffic_total_bytes + increment,
                traffic_last_live_bytes=0
            )
            .returning(User.traffic_total_bytes)
        )
        result = await session.execute(stmt)
        updated_total = result.scalar_one_or_none()
        
        if updated_total is None:
            return increment
            
        return updated_total
