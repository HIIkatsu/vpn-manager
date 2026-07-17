from datetime import datetime, timedelta, timezone
import json
import logging
import asyncio
from typing import TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import User
from app.core.security import DistributedLock
from app.core.logging_utils import log_context
from app.core.settings import settings
from app.db.repositories.payment_repo import PaymentRepository
from app.db.repositories.user_repo import UserRepository
from app.services.xray_manager import XrayManager
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
        notifier: "Bot",
        outbox: OutboxRepository | None = None,
    ):
        self.session = session
        self.users = users
        self.payments = payments
        self.xray_manager = xray_manager
        self.notifier = notifier
        self.outbox = outbox or OutboxRepository(session)
        self.logger = logging.getLogger(__name__)
        self._processing_lock = DistributedLock()

    async def activate_payment(self, payment_id: str, event_id: str | None = None) -> bool:
        payment = await self.payments.get_by_payment_id_for_update(payment_id)
        if payment is None: return False
        if payment.status == "success": return True
        if payment.status == "processing": return False
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
            if event_id: payment.processed_event_id = event_id
            user.is_active = True
            amt = float(payment.amount)
            days = 365 if amt >= 900.0 else 90 if amt >= 250.0 else 30
            now = datetime.now(timezone.utc)
            if user.sub_end_date is None or user.sub_end_date < now:
                user.sub_end_date = now + timedelta(days=days)
            else:
                user.sub_end_date += timedelta(days=days)
            await self.session.flush()
            await self.outbox.enqueue(
                event_type="xray.add_client", aggregate_type="payment", aggregate_id=payment.payment_id,
                dedup_key=f"xray.add_client:{payment.payment_id}",
                payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid})
            )
            
            try:
                msg = f"✅ <b>Оплата успешно получена!</b>\n\nВаша подписка активна до: <b>{user.sub_end_date.strftime('%d.%m.%Y')}</b>"
                await self.notifier.send_message(chat_id=user.telegram_id, text=msg, parse_mode="HTML")
            except Exception as e:
                self.logger.warning(f"Failed to notify user {user.telegram_id}: {e}")
                
            if getattr(user, 'referrer_telegram_id', None):
                try:
                    ref_res = await self.session.execute(select(User).where(User.telegram_id == user.referrer_telegram_id))
                    referrer = ref_res.scalars().first()
                    if referrer:
                        if referrer.sub_end_date is None or referrer.sub_end_date < now: referrer.sub_end_date = now + timedelta(days=7)
                        else: referrer.sub_end_date += timedelta(days=7)
                        referrer.is_active = True
                        await self.notifier.send_message(chat_id=referrer.telegram_id, text="🎁 <b>По вашей ссылке зарегистрировался друг!</b>\n\nВам начислено <b>+7 дней</b>.")
                        await self.outbox.enqueue(
                            event_type="xray.add_client", aggregate_type="referral_reward", aggregate_id=str(referrer.id),
                            dedup_key=f"xray.add_client:ref_{referrer.id}_{int(now.timestamp())}",
                            payload_json=json.dumps({"telegram_id": referrer.telegram_id, "uuid": referrer.vless_uuid})
                        )
                except Exception:
                    self.logger.exception("Failed to process referral reward")
                finally:
                    user.referrer_telegram_id = None
            await self.session.flush()
            return True
        except Exception:
            payment.status = "pending"
            payment.processing_started_at = None
            await self.session.flush()
            raise

    async def reclaim_stale_processing(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.BILLING_PROCESSING_STALE_AFTER_SECONDS)
        stale = await self.payments.get_stale_processing(started_before=cutoff, limit=settings.BILLING_PENDING_BATCH_SIZE)
        recovered = 0
        for payment in stale:
            payment.status = "pending"
            payment.processing_started_at = None
            recovered += 1
        if recovered: await self.session.flush()
        return recovered

    async def process_pending(self) -> None:
        if not self._processing_lock.acquire("billing:process_pending", ttl_seconds=60): return
        reclaimed = await self.reclaim_stale_processing()
        if reclaimed: self.logger.warning(f"Reclaimed {reclaimed} stale processing payments")

    async def notify_expiring_subscriptions(self, days_before: int = 3) -> None:
        expiring_users = await self.users.get_expiring_in_days(days_before)
        for user in expiring_users:
            try:
                await self.notifier.send_message(chat_id=user.telegram_id, text=f"⏰ Напоминание: подписка закончится через {days_before} дня(дней).")
            except Exception: continue
