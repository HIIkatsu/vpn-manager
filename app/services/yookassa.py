import asyncio
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from yookassa import Configuration, Payment as YooPayment

from app.core.config import settings
from app.db.models import Payment


Configuration.account_id = settings.YOOKASSA_SHOP_ID
Configuration.secret_key = settings.YOOKASSA_SECRET_KEY


async def create_payment(session: AsyncSession, user_id: int, amount: float) -> str:
    payment_data = {
        "amount": {
            "value": f"{Decimal(str(amount)):.2f}",
            "currency": "RUB",
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/NeuroVPN_AI_bot",
        },
        "description": "Продление VPN-подписки на 30 дней",
        "metadata": {
            "user_id": str(user_id),
        },
    }
    payment = await asyncio.to_thread(
        YooPayment.create,
        payment_data,
        str(uuid.uuid4()),
    )

    db_payment = Payment(
        user_id=user_id,
        payment_id=payment.id,
        amount=Decimal(str(amount)),
        status="pending",
    )
    session.add(db_payment)
    await session.commit()

    return payment.confirmation.confirmation_url
