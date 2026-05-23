from datetime import datetime, timedelta, timezone
import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.settings import settings
from app.db.repositories.payment_repo import PaymentRepository
from app.db.repositories.user_repo import UserRepository
from app.services.xray_manager import XrayManager
from app.services.yookassa_service import YooKassaService

class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        payments: PaymentRepository,
        xray_manager: XrayManager,
        yookassa_service: YooKassaService,
        notifier: Bot,
    ):
        self.session = session
        self.users = users
        self.payments = payments
        self.xray_manager = xray_manager
        self.yookassa_service = yookassa_service
        self.notifier = notifier
        self.logger = logging.getLogger(__name__)

    async def create_subscription_payment(self, user_id: int, amount: float) -> str:
        url = await self.yookassa_service.create_payment(self.payments, user_id, amount)
        await self.session.flush()
        return url

    async def activate_payment(self, payment_id: str, event_id: str | None = None) -> bool:
        payment = await self.payments.get_by_payment_id_for_update(payment_id)
        if payment is None:
            return False
        if payment.status == "success":
            return True
        if payment.status == "processing":
            return False

        payment.status = "processing"
        await self.session.flush()
        
        user = await self.users.get_by_id(payment.user_id)
        if user is None:
            payment.status = "pending"
            await self.session.flush()
            return False
            
        xray_ok = await self.xray_manager.add_client(email=str(user.telegram_id), uuid=user.vless_uuid)
        if not xray_ok:
            payment.status = "pending"
            await self.session.flush()
            return False

        payment.status = "success"
        if event_id:
            payment.processed_event_id = event_id
        user.is_active = True

        days = 30
        amt = float(payment.amount)
        if amt == 250.0:
            days = 90
        elif amt == 900.0:
            days = 365

        now = datetime.now(timezone.utc)
        if user.sub_end_date is None or user.sub_end_date < now:
            user.sub_end_date = now + timedelta(days=days)
        else:
            user.sub_end_date += timedelta(days=days)

        await self.session.flush()
        return True
    async def process_pending(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        pending_payments = await self.payments.get_pending_before(
            older_than=cutoff,
            limit=settings.BILLING_PENDING_BATCH_SIZE,
        )
        payment_ids = [p.payment_id for p in pending_payments]

        for pid in payment_ids:
            remote_payment = await self.yookassa_service.fetch_remote_payment(pid)
            if remote_payment.status == "succeeded":
                activated = await self.activate_payment(pid)
                if not activated:
                    self.logger.warning(
                        "Pending payment compensation failed",
                        extra={"payment_id": pid, "source": "process_pending"},
                    )


    async def notify_expiring_subscriptions(self, days_before: int = 3) -> None:
        expiring_users = await self.users.get_expiring_in_days(days_before)
        for user in expiring_users:
            try:
                await self.notifier.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        f"⏰ Напоминание: подписка закончится через {days_before} дня(дней).\n"
                        "Откройте раздел 💳 Подписка, чтобы продлить доступ без перерывов."
                    ),
                )
            except Exception:
                continue
