import asyncio
import base64
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from yookassa import Configuration, Payment as YooPayment
from yookassa.domain.notification import WebhookNotificationFactory

from app.core.config import settings
from app.db.models import Payment


Configuration.account_id = settings.YOOKASSA_SHOP_ID
Configuration.secret_key = settings.YOOKASSA_SECRET_KEY


def _expected_basic_auth() -> str:
    token = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
    return f"Basic {base64.b64encode(token).decode('ascii')}"


def is_valid_yookassa_webhook_auth(authorization_header: str | None) -> bool:
    if not authorization_header:
        return False
    return authorization_header.strip() == _expected_basic_auth()


def parse_yookassa_notification(payload: dict):
    try:
        return WebhookNotificationFactory().create(payload)
    except Exception:
        return None


async def fetch_remote_payment(payment_id: str):
    return await asyncio.to_thread(YooPayment.find_one, payment_id)


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
