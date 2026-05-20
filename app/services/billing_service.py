from datetime import datetime, timedelta, timezone
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
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

    async def create_subscription_payment(self, user_id: int, amount: float) -> str:
        url = await self.yookassa_service.create_payment(self.payments, user_id, amount)
        await self.session.commit()
        return url

    async def activate_payment(self, payment_id: str) -> bool:
        payment = await self.payments.get_by_payment_id(payment_id)
        if payment is None or payment.status == "success":
            return True
            
        user = await self.users.get_by_id(payment.user_id)
        if user is None:
            return False
            
        # Пытаемся добавить клиента. XrayManager теперь не падает, если юзер уже есть
        xray_ok = await self.xray_manager.add_client(email=str(user.telegram_id), uuid=user.vless_uuid)
        if not xray_ok:
            return False
            
        payment.status = "success"
        user.is_active = True
        
        # Логика продления
        now = datetime.now(timezone.utc)
        if user.sub_end_date is None or user.sub_end_date < now:
            user.sub_end_date = now + timedelta(days=30)
        else:
            user.sub_end_date += timedelta(days=30)
            
        await self.session.commit()
        # Обновляем состояние объекта, чтобы шедулер видел изменения
        self.session.expire(payment)
        
        await self.notifier.send_message(user.telegram_id, "✅ Оплата получена. Доступ выдан/продлен!")
        return True

    async def process_pending(self) -> None:
        pending_payments = await self.payments.get_pending()
        payment_ids = [p.payment_id for p in pending_payments]

        for pid in payment_ids:
            remote_payment = await self.yookassa_service.fetch_remote_payment(pid)
            if remote_payment.status == "succeeded":
                await self.activate_payment(pid)
