import asyncio
import base64
import hmac
import uuid
import logging
from decimal import Decimal

try:
    from yookassa import Configuration, Payment as YooPayment
    from yookassa.domain.notification import WebhookNotificationFactory
except Exception:
    Configuration = None
    YooPayment = None
    WebhookNotificationFactory = None

from app.core.settings import settings
from app.core.logging_utils import log_context
from app.db.models import Payment
from app.db.repositories.payment_repo import PaymentRepository


class YooKassaService:
    def __init__(self) -> None:
        if Configuration is not None:
            Configuration.account_id = settings.YOOKASSA_SHOP_ID
            Configuration.secret_key = settings.YOOKASSA_SECRET_KEY

    def expected_basic_auth(self) -> str:
        token = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
        return f"Basic {base64.b64encode(token).decode('ascii')}"

    def _is_valid_basic_auth(self, authorization_header: str | None) -> bool:
        if not authorization_header:
            return False

        configured_auth = settings.YOOKASSA_WEBHOOK_AUTH
        if configured_auth and hmac.compare_digest(authorization_header.strip(), configured_auth):
            return True

        return hmac.compare_digest(authorization_header.strip(), self.expected_basic_auth())

    def _is_valid_webhook_secret(self, token: str | None) -> bool:
        expected = settings.YOOKASSA_WEBHOOK_SECRET
        if not expected or not token:
            return False
        return hmac.compare_digest(token.strip(), expected)

    def is_valid_webhook_auth(
        self,
        authorization_header: str | None,
        webhook_secret_header: str | None,
    ) -> bool:
        if self._is_valid_basic_auth(authorization_header):
            return True
        return self._is_valid_webhook_secret(webhook_secret_header)

    def parse_notification(self, payload: dict):
        if WebhookNotificationFactory is None:
            return None
        try:
            return WebhookNotificationFactory().create(payload)
        except Exception:
            return None

    async def fetch_remote_payment(self, payment_id: str):
        if YooPayment is None:
            raise RuntimeError("yookassa SDK is not installed")
        last_error: Exception | None = None
        for attempt in range(settings.YOOKASSA_REQUEST_RETRIES + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(YooPayment.find_one, payment_id),
                    timeout=settings.YOOKASSA_REQUEST_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                last_error = exc
                logging.getLogger(__name__).warning(
                    "YooKassa find_one failed",
                    extra=log_context(
                        payment_id=payment_id,
                        action_source="yookassa_find_payment",
                        attempt=attempt + 1,
                        endpoint="yookassa.find_one",
                    ),
                )
                if attempt < settings.YOOKASSA_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2 ** attempt))
        raise RuntimeError(f"YooKassa find_one failed after retries for payment {payment_id}") from last_error

    async def create_payment(self, payments: PaymentRepository, user_id: int, amount: float, return_url: str = None) -> str:
        if YooPayment is None:
            raise RuntimeError("yookassa SDK is not installed")
        
        if not return_url:
            return_url = "tg://resolve?domain=NeuroVPN_AI_bot"

        payment_data = {
            "amount": {"value": f"{Decimal(str(amount)):.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": "Продление VPN-подписки",
            "metadata": {"user_id": str(user_id)},
        }
        payment = await asyncio.to_thread(YooPayment.create, payment_data, str(uuid.uuid4()))
        
        new_payment = Payment(user_id=user_id, payment_id=payment.id, amount=Decimal(str(amount)), status="pending")
        
        try:
            res = payments.add(new_payment)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            if hasattr(payments, 'session'):
                payments.session.add(new_payment)
                
        return payment.confirmation.confirmation_url
