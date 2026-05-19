from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

# Импорт бота отсюда убрали, чтобы разорвать кольцо

from app.db.database import async_session_maker
from app.db.repositories.payment_repo import PaymentRepository
from app.db.repositories.user_repo import UserRepository
from app.services.billing_service import BillingService
from app.services.user_service import UserService
from app.services.xray_manager import XrayManager
from app.services.yookassa_service import YooKassaService

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session

def get_xray_manager() -> XrayManager:
    return XrayManager()

def get_yookassa_service() -> YooKassaService:
    return YooKassaService()

def get_user_service(session: AsyncSession) -> UserService:
    return UserService(UserRepository(session))

def get_billing_service(session: AsyncSession) -> BillingService:
    # Ленивый импорт — вызывается только в момент создания сервиса
    from app.bot.core import bot
    
    return BillingService(
        session=session,
        users=UserRepository(session),
        payments=PaymentRepository(session),
        xray_manager=get_xray_manager(),
        yookassa_service=get_yookassa_service(),
        notifier=bot,
    )
