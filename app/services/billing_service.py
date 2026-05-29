from datetime import datetime, timedelta, timezone
import json
import logging
import asyncio
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import DistributedLock
from app.core.logging_utils import log_context
from app.core.settings import settings
from app.db.repositories.payment_repo import PaymentRepository
from app.db.repositories.user_repo import UserRepository
from app.services.xray_manager import XrayManager
from app.services.yookassa_service import YooKassaService
from app.db.repositories.outbox_repo import OutboxRepository

if TYPE_CHECKING:
    from aiogram import Bot

class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        payments: PaymentRepository,
        xray_manager: XrayManager,
        yookassa_service: YooKassaService,
        notifier: "Bot",
        outbox: OutboxRepository | None = None,
    ):
        self.session = session
        self.users = users
        self.payments = payments
        self.xray_manager = xray_manager
        self.yookassa_service = yookassa_service
        self.notifier = notifier
        self.outbox = outbox or OutboxRepository(session)
        self.logger = logging.getLogger(__name__)
        self._processing_lock = DistributedLock()

    async def create_subscription_payment(self, user_id: int, amount: float, return_url: str = None) -> str:
        import inspect
        sig = inspect.signature(self.yookassa_service.create_payment)
        
        if 'return_url' in sig.parameters:
            url = await self.yookassa_service.create_payment(self.payments, user_id, amount, return_url=return_url)
        else:
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
        payment.processing_started_at = datetime.now(timezone.utc)
        await self.session.flush()
        
        try:
            user = await self.users.get_by_id(payment.user_id)
            if user is None:
                payment.status = "pending"
                payment.processing_started_at = None
                await self.session.flush()
                return False

            payment.status = "success"
            payment.processing_started_at = None
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
            await self.outbox.enqueue(
                event_type="xray.add_client",
                aggregate_type="payment",
                aggregate_id=payment.payment_id,
                dedup_key=f"xray.add_client:{payment.payment_id}",
                payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid}),
            )

            await self.session.flush()
            return True
        except Exception:
            payment.status = "pending"
            payment.processing_started_at = None
            await self.session.flush()
            raise

    async def reclaim_stale_processing(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.BILLING_PROCESSING_STALE_AFTER_SECONDS)
        stale = await self.payments.get_stale_processing(
            started_before=cutoff,
            limit=settings.BILLING_PENDING_BATCH_SIZE,
        )
        recovered = 0
        for payment in stale:
            payment.status = "pending"
            payment.processing_started_at = None
            recovered += 1
        if recovered:
            await self.session.flush()
        return recovered

    async def process_pending(self) -> None:
        if not self._processing_lock.acquire("billing:process_pending", ttl_seconds=60):
            self.logger.info(
                "Skipped process_pending due to active distributed lock",
                extra=log_context(action_source="process_pending", endpoint="distributed_lock"),
            )
            return

        reclaimed = await self.reclaim_stale_processing()
        if reclaimed:
            self.logger.warning(
                "Reclaimed stale processing payments",
                extra=log_context(count=reclaimed, action_source="process_pending"),
            )

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        pending_payments = await self.payments.get_pending_before(
            older_than=cutoff,
            limit=settings.BILLING_PENDING_BATCH_SIZE,
        )
        payment_ids = [p.payment_id for p in pending_payments]

        for pid in payment_ids:
            remote_payment = await asyncio.wait_for(
                self.yookassa_service.fetch_remote_payment(pid),
                timeout=settings.YOOKASSA_REQUEST_TIMEOUT_SECONDS * (settings.YOOKASSA_REQUEST_RETRIES + 2),
            )
            if remote_payment.status == "succeeded":
                activated = await self.activate_payment(pid)
                if not activated:
                    self.logger.warning(
                        "Pending payment compensation failed",
                        extra=log_context(payment_id=pid, action_source="process_pending"),
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
