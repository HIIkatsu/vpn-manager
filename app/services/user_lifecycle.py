from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PendingAction, User


async def delete_user_with_relations(session: AsyncSession, user: User) -> None:
    await session.execute(delete(Payment).where(Payment.user_id == user.id))
    await session.execute(delete(PendingAction).where(PendingAction.user_id == user.id))
    await session.delete(user)
