import asyncio
import base64
import hmac
import logging
import uuid
from decimal import Decimal
from hashlib import sha256

from yookassa import Configuration, Payment as YooPayment
from yookassa.domain.notification import WebhookNotificationFactory

from app.core.settings import settings
from app.db.models import Payment
from app.db.repositories.payment_repo import PaymentRepository


class YooKassaService:
    def __init__(self) -> None:
        Configuration.account_id = settings.YOOKASSA_SHOP_ID
        Configuration.secret_key = settings.YOOKASSA_SECRET_KEY

    def expected_basic_auth(self) -> str:
        token = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
        return f"Basic {base64.b64encode(token).decode('ascii')}"

    def _is_valid_basic_auth(self, authorization_header: str | None) -> bool:
        return bool(
            authorization_header
            and hmac.compare_digest(authorization_header.strip(), self.expected_basic_auth())
        )

    def _expected_hmac_signature(self, body_bytes: bytes) -> str:
        secret = settings.YOOKASSA_SECRET_KEY.encode("utf-8")
        return hmac.new(secret, body_bytes, sha256).hexdigest()

    def _is_valid_hmac_auth(self, signature_header: str | None, body_bytes: bytes) -> bool:
        if not signature_header:
            return False
        candidate = signature_header.strip()
        expected = self._expected_hmac_signature(body_bytes)
        return hmac.compare_digest(candidate, expected)

    def is_valid_webhook_auth(
        self,
        authorization_header: str | None,
        signature_header: str | None = None,
        body_bytes: bytes | None = None,
    ) -> bool:
        if self._is_valid_basic_auth(authorization_header):
            return True
        if body_bytes is None:
            return False
        return self._is_valid_hmac_auth(signature_header, body_bytes)

    def parse_notification(self, payload: dict):
        try:
            return WebhookNotificationFactory().create(payload)
        except Exception:
            return None

    async def fetch_remote_payment(self, payment_id: str):
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
                    extra={"payment_id": payment_id, "attempt": attempt + 1},
                )
                if attempt < settings.YOOKASSA_REQUEST_RETRIES:
                    await asyncio.sleep(0.2 * (2 ** attempt))
        raise RuntimeError(f"YooKassa find_one failed after retries for payment {payment_id}") from last_error

    async def create_payment(self, payments: PaymentRepository, user_id: int, amount: float) -> str:
        payment_data = {
            "amount": {"value": f"{Decimal(str(amount)):.2f}", "currency": "RUB"},
            "capture": True,
            # Вот здесь меняем веб-ссылку на жесткий диплинк
            "confirmation": {"type": "redirect", "return_url": "tg://resolve?domain=NeuroVPN_AI_bot"},
            "description": "Продление VPN-подписки на 30 дней",
            "metadata": {"user_id": str(user_id)},
        }
        payment = await asyncio.to_thread(YooPayment.create, payment_data, str(uuid.uuid4()))
        await payments.add(
            Payment(user_id=user_id, payment_id=payment.id, amount=Decimal(str(amount)), status="pending")
        )
        return payment.confirmation.confirmation_url
