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
