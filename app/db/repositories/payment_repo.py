from sqlalchemy import select

from app.db.models import Payment
from app.db.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_payment_id(self, payment_id: str) -> Payment | None:
        return await self.session.scalar(select(Payment).where(Payment.payment_id == payment_id))

    async def get_pending(self) -> list[Payment]:
        result = await self.session.scalars(select(Payment).where(Payment.status == "pending"))
        return list(result.all())

    async def get_by_payment_id_for_update(self, payment_id: str) -> Payment | None:
        stmt = select(Payment).where(Payment.payment_id == payment_id).with_for_update()
        return await self.session.scalar(stmt)

    async def get_latest_by_user_id(self, user_id: int) -> Payment | None:
        stmt = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)
