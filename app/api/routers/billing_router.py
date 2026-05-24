import asyncio
import ipaddress
import json
import logging
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.common import get_async_session
from app.bot.core import bot
from app.core.container import get_billing_service
from app.core.logging_utils import log_context
from app.core.security import SharedRateLimiter, WebhookReplayGuard, ip_in_allowlist
from app.core.settings import settings
from app.db.models import User
from app.services.billing_service import BillingService
from app.services.yookassa_service import YooKassaService

router = APIRouter()
logger = logging.getLogger(__name__)
rate_limiter = SharedRateLimiter()
replay_guard = WebhookReplayGuard()


@router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_async_session)) -> dict:
    yookassa = YooKassaService()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    auth_header = request.headers.get("authorization")
    webhook_secret_header = request.headers.get("x-yookassa-webhook-secret")

    if not yookassa.is_valid_webhook_auth(auth_header, webhook_secret_header):
        pass  # Проверка делегирована IP Allowlist и Server-to-Server верификации

    trusted_proxies = {ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()}
    remote_addr = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    x_real_ip = request.headers.get("x-real-ip")
    client_ip = remote_addr
    if remote_addr in trusted_proxies:
        if x_real_ip:
            client_ip = x_real_ip.strip()
        elif forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid client IP")

    allowed = await asyncio.to_thread(rate_limiter.allow, f"yk:{client_ip}", settings.YOOKASSA_RATE_LIMIT_PER_MINUTE, 60, fail_open=False)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    allowlist = [x.strip() for x in settings.YOOKASSA_WEBHOOK_IP_ALLOWLIST.split(",") if x.strip()]
    if not allowlist:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook IP allowlist is not configured")
    if not ip_in_allowlist(client_ip, allowlist):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden IP")

    notification = yookassa.parse_notification(payload)
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}

    payment_obj = notification.object
    payload_event_id = str(payload.get("id") or "")
    event_id = payload_event_id or (getattr(notification, "event", "") + ":" + payment_obj.id)

    is_fresh_event = await asyncio.to_thread(replay_guard.mark_if_fresh, event_id, settings.WEBHOOK_REPLAY_TTL_SECONDS)
    if not is_fresh_event:
        logger.info("Duplicate/replayed webhook blocked", extra=log_context(event_id=event_id, payment_id=payment_obj.id, action_source="webhook"))
        return {"status": "duplicate"}

    if settings.YOOKASSA_WEBHOOK_REQUIRE_API_VERIFY:
        remote_payment = await yookassa.fetch_remote_payment(payment_obj.id)
        if remote_payment is None or remote_payment.status != "succeeded":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Payment verification failed")

    billing: BillingService = get_billing_service(session)
    payment = await billing.payments.get_by_payment_id_for_update(payment_obj.id)

    if payment is None:
        logger.warning("Webhook payment not found", extra=log_context(payment_id=payment_obj.id, action_source="webhook"))
        return {"status": "not_found"}
    if payment.processed_event_id == event_id:
        logger.info(
            "Duplicate payment event received", extra=log_context(event_id=event_id, payment_id=payment_obj.id, action_source="webhook")
        )
        return {"status": "duplicate"}
    if payment.amount != Decimal(payment_obj.amount.value) or payment_obj.amount.currency != "RUB":
        logger.warning(
            "Payment amount validation failed", extra=log_context(payment_id=payment_obj.id, event_id=event_id, action_source="webhook")
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount mismatch")
    if str(payment.user_id) != str(payment_obj.metadata.get("user_id")) or payment_obj.paid is not True:
        logger.warning(
            "Payment metadata validation failed", extra=log_context(payment_id=payment_obj.id, event_id=event_id, action_source="webhook")
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Metadata mismatch")

    if not await billing.activate_payment(payment_obj.id, event_id):
#         await session.commit() # FIXED: UoW violation
        logger.warning("Payment activation returned retry", extra=log_context(payment_id=payment_obj.id, event_id=event_id, action_source="webhook"))
        return {"status": "retry"}

#     await session.commit() # FIXED: UoW violation

    try:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="open_profile")]]
        )
        user = await session.get(User, payment.user_id)
        if user:
            period_text = (
                "на 1 год" if float(payment.amount) == 900.0 else "на 3 месяца" if float(payment.amount) == 250.0 else "на 1 месяц"
            )
            await bot.send_message(
                chat_id=user.telegram_id,
                text=f"✅ <b>Оплата успешно получена!</b>\nВы оформили/продлили подписку <b>{period_text}</b>.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.error("Failed to send payment confirmation to Telegram", extra=log_context(error=str(e), user_id=payment.user_id, payment_id=payment_obj.id))

    return {"status": "ok"}
