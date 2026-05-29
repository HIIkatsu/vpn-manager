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

    # SECURITY FIX 1: Проверка IP-адреса ЮKassa
    allowed_ips = getattr(settings, 'YOOKASSA_WEBHOOK_IP_ALLOWLIST', "")
    if allowed_ips:
        cidrs = [x.strip() for x in allowed_ips.split(",") if x.strip()]
        if not ip_in_allowlist(client_ip, cidrs):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden IP")

    # SECURITY FIX 2: Проверка Webhook Auth
    if getattr(settings, 'YOOKASSA_WEBHOOK_AUTH', None) or getattr(settings, 'YOOKASSA_WEBHOOK_SECRET', None):
        if hasattr(yookassa, 'is_valid_webhook_auth') and not yookassa.is_valid_webhook_auth(request):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook auth")

    allowed = await asyncio.to_thread(rate_limiter.allow, f"yk:{client_ip}", settings.YOOKASSA_RATE_LIMIT_PER_MINUTE, 60, fail_open=False)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    notification = yookassa.parse_notification(payload)
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}
        
    payment_obj = notification.object
    payload_event_id = str(payload.get("id") or "")
    event_id = payload_event_id or (getattr(notification, "event", "") + ":" + payment_obj.id)

    if True:
        remote_payment = await yookassa.fetch_remote_payment(payment_obj.id)
        if remote_payment is None or remote_payment.status != "succeeded":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Payment verification failed")
            
    billing: BillingService = get_billing_service(session)
    payment = await billing.payments.get_by_payment_id_for_update(payment_obj.id)
    if payment is None:
        logger.warning("Webhook payment not found", extra=log_context(payment_id=payment_obj.id, action_source="webhook"))
        return {"status": "not_found"}

    # SECURITY FIX 3: Replay Guard проверяется ПОСЛЕ нахождения платежа в БД
    is_fresh_event = await asyncio.to_thread(replay_guard.mark_if_fresh, event_id, settings.WEBHOOK_REPLAY_TTL_SECONDS)
    if not is_fresh_event:
        logger.info("Duplicate/replayed webhook blocked", extra=log_context(event_id=event_id, payment_id=payment_obj.id, action_source="webhook"))
        return {"status": "duplicate"}

    if payment.processed_event_id == event_id:
        return {"status": "duplicate"}
        
    if payment.amount != Decimal(payment_obj.amount.value) or payment_obj.amount.currency != "RUB":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount mismatch")
        
    if str(payment.user_id) != str(payment_obj.metadata.get("user_id")) or payment_obj.paid is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Metadata mismatch")
        
    if not await billing.activate_payment(payment_obj.id, event_id):
        return {"status": "retry"}

    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="open_profile")]])
        user = await session.get(User, payment.user_id)
        if user:
            period_text = "на 1 год" if float(payment.amount) == 900.0 else "на 3 месяца" if float(payment.amount) == 250.0 else "на 1 месяц"
            await bot.send_message(chat_id=user.telegram_id, text=f"✅ <b>Оплата успешно получена!</b>\nВы оформили/продлили подписку <b>{period_text}</b>.", parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error("Failed to send payment confirmation to Telegram", extra=log_context(error=str(e), user_id=payment.user_id, payment_id=payment_obj.id))
        
    return {"status": "ok"}
