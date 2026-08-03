import hashlib
import hmac
import json
import logging
from datetime import datetime as dt, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.common import get_write_session
from app.bot.core import bot
from app.core.container import get_billing_service
from app.core.logging_utils import log_context
from app.core.security import (
    SharedRateLimiter,
    WebhookReplayGuard,
    client_ip_from_request,
    ip_in_allowlist,
    split_csv,
)
from app.core.settings import settings
from app.db.models import OutboxEvent, Payment, User
from app.db.repositories.outbox_repo import OutboxRepository
from app.services.yookassa_service import YooKassaService

router = APIRouter()
logger = logging.getLogger(__name__)
rate_limiter = SharedRateLimiter()
webhook_replay_guard = WebhookReplayGuard()


def _enforce_rate_limit(key: str, limit: int) -> None:
    if not rate_limiter.allow(key, limit, 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")


async def activate_subscription(session: AsyncSession, telegram_id: int, days: int, payment_source: str, tx_id: str):
    dedup_key = f"xray.add_client:pay_{payment_source}_{tx_id}"

    if await session.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key)):
        return True

    user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
    if not user:
        return False

    now = dt.now(timezone.utc)
    if user.sub_end_date is None or user.sub_end_date < now:
        user.sub_end_date = now + timedelta(days=days)
    else:
        user.sub_end_date += timedelta(days=days)
    user.is_active = True

    outbox = OutboxRepository(session)
    await outbox.enqueue(
        event_type="xray.add_client",
        aggregate_type=f"{payment_source}_payment",
        aggregate_id=str(user.id),
        dedup_key=dedup_key,
        payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid}),
    )
    await session.commit()

    try:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="menu_profile")]]
        )
        await bot.send_message(
            chat_id=telegram_id,
            text=f"✅ <b>Оплата успешно получена!</b>\n\nВам начислено <b>+{days} дней</b> премиум-доступа.",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("Failed to send payment success notification", extra=log_context(telegram_id=telegram_id))
    return True


@router.post("/api/yookassa/webhook")
@router.post("/api/yookassa/webhook/")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    client_ip = client_ip_from_request(request)
    _enforce_rate_limit(f"webhook:yookassa:{client_ip}", settings.YOOKASSA_RATE_LIMIT_PER_MINUTE)

    allowlist = split_csv(settings.YOOKASSA_WEBHOOK_IP_ALLOWLIST)
    if allowlist and not ip_in_allowlist(client_ip, allowlist):
        logger.warning("Rejected YooKassa webhook from non-allowlisted IP", extra=log_context(endpoint="yookassa_webhook"))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    yk_service = YooKassaService()
    if not yk_service.is_valid_webhook_auth(
        request.headers.get("authorization"),
        request.headers.get("x-yookassa-webhook-secret"),
    ):
        logger.warning("Rejected YooKassa webhook with invalid auth", extra=log_context(endpoint="yookassa_webhook"))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    try:
        payload = await request.json()
        notification = yk_service.parse_notification(payload)
        if not notification or notification.event != "payment.succeeded":
            return Response(status_code=200, content="OK")

        payment_id = notification.object.id
        replay_key = f"yookassa:{payload.get('event')}:{payment_id}"

        payment = await session.scalar(select(Payment).where(Payment.payment_id == payment_id))
        if payment and payment.status == "success":
            return Response(status_code=200, content="OK")
        if payment is None:
            logger.warning("Rejected YooKassa webhook for unknown payment", extra=log_context(payment_id=payment_id))
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

        if settings.YOOKASSA_WEBHOOK_REQUIRE_API_VERIFY:
            remote = await yk_service.fetch_remote_payment(payment_id)
            if not remote or remote.status != "succeeded":
                logger.warning("Rejected YooKassa webhook because remote status is not succeeded", extra=log_context(payment_id=payment_id))
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is not confirmed")

            remote_user_id = str(getattr(remote, "metadata", {}).get("user_id", ""))
            if remote_user_id != str(payment.user_id):
                logger.warning("Rejected YooKassa webhook with metadata mismatch", extra=log_context(payment_id=payment_id))
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment metadata mismatch")

            try:
                remote_amount = Decimal(str(remote.amount.value))
            except (AttributeError, InvalidOperation) as exc:
                logger.warning("Rejected YooKassa webhook with invalid remote amount", extra=log_context(payment_id=payment_id))
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invalid payment amount") from exc
            if remote_amount != payment.amount:
                logger.warning("Rejected YooKassa webhook with amount mismatch", extra=log_context(payment_id=payment_id))
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment amount mismatch")

        if not webhook_replay_guard.mark_if_fresh(replay_key, settings.WEBHOOK_REPLAY_TTL_SECONDS):
            logger.info("Ignored duplicate YooKassa webhook", extra=log_context(payment_id=payment_id))
            return Response(status_code=200, content="OK")

        billing = get_billing_service(session)
        await billing.activate_payment(payment_id, replay_key)
        await session.commit()
        return Response(status_code=200, content="OK")
    except HTTPException:
        raise
    except Exception:
        logger.exception("YooKassa webhook processing failed", extra=log_context(endpoint="yookassa_webhook"))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing failed")


@router.post("/api/payments/anypay-webhook")
async def anypay_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    client_ip = client_ip_from_request(request)
    _enforce_rate_limit(f"webhook:anypay:{client_ip}", settings.WEBHOOK_RATE_LIMIT_PER_MINUTE)

    if not settings.ANYPAY_SECRET_KEY:
        logger.error("AnyPay webhook rejected because ANYPAY_SECRET_KEY is not configured")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Payment provider is not configured")

    form_data = await request.form()
    merchant_id = form_data.get("merchant_id")
    amount = form_data.get("amount")
    pay_id = form_data.get("pay_id", "")
    status_pay = form_data.get("status")
    received_sign = form_data.get("sign")
    transaction_id = form_data.get("transaction_id", "unknown")
    currency = form_data.get("currency", "RUB")

    if not all([merchant_id, amount, pay_id, received_sign]):
        return Response(content="ERROR", status_code=400)
    if settings.ANYPAY_PROJECT_ID and not hmac.compare_digest(str(merchant_id), settings.ANYPAY_PROJECT_ID):
        logger.warning("Rejected AnyPay webhook with merchant mismatch")
        return Response(content="Forbidden", status_code=403)

    secret = settings.ANYPAY_SECRET_KEY
    valid_signs = [
        hashlib.sha256(f"{currency}:{amount}:{pay_id}:{merchant_id}:{status_pay}:{secret}".encode()).hexdigest(),
        hashlib.sha256(f"{merchant_id}:{amount}:{pay_id}:{secret}".encode()).hexdigest(),
    ]
    if not any(hmac.compare_digest(str(received_sign), sign) for sign in valid_signs):
        logger.warning("Rejected AnyPay webhook with invalid signature")
        return Response(content="Forbidden", status_code=403)
    if status_pay != "paid":
        return Response(content="OK", status_code=200)

    replay_key = f"anypay:{transaction_id}"
    if not webhook_replay_guard.mark_if_fresh(replay_key, settings.WEBHOOK_REPLAY_TTL_SECONDS):
        return Response(content="OK", status_code=200)

    try:
        tg_id = int(str(pay_id)) if len(str(pay_id)) <= 11 else int(str(pay_id)[:-3])
        days = 365 if float(amount) >= 900 else 90 if float(amount) >= 250 else 30
        await activate_subscription(session, tg_id, days, "anypay", str(transaction_id))
    except Exception:
        logger.exception("AnyPay webhook processing failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing failed")
    return Response(content="OK", status_code=200)


@router.post("/api/cryptobot/webhook")
async def cryptobot_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    client_ip = client_ip_from_request(request)
    _enforce_rate_limit(f"webhook:cryptobot:{client_ip}", settings.WEBHOOK_RATE_LIMIT_PER_MINUTE)

    if not settings.CRYPTOBOT_TOKEN:
        logger.error("CryptoBot webhook rejected because CRYPTOBOT_TOKEN is not configured")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Payment provider is not configured")

    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not signature:
        return Response(status_code=400, content="Missing signature")

    secret = hashlib.sha256(settings.CRYPTOBOT_TOKEN.encode()).digest()
    expected = hmac.new(secret, body, digestmod=hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("Rejected CryptoBot webhook with invalid signature")
        return Response(status_code=403, content="Invalid signature")

    try:
        data = json.loads(body)
        if data.get("update_type") == "invoice_paid":
            invoice_id = str(data["payload"]["invoice_id"])
            replay_key = f"cryptobot:{invoice_id}"
            if not webhook_replay_guard.mark_if_fresh(replay_key, settings.WEBHOOK_REPLAY_TTL_SECONDS):
                return Response(status_code=200, content="OK")
            parts = data["payload"]["payload"].split("_")
            if len(parts) >= 2:
                await activate_subscription(session, int(parts[0]), int(parts[1]), "crypto", invoice_id)
    except Exception:
        logger.exception("CryptoBot webhook processing failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing failed")
    return Response(status_code=200, content="OK")

from fastapi.responses import HTMLResponse

@router.get("/api/payments/redirect_to_bot")
async def redirect_to_bot():
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Возврат в бота...</title>
        <script>
            window.onload = function() {
                try {
                    window.top.location.href = "tg://resolve?domain=AnKoVPN_bot";
                } catch (e) {
                    window.location.href = "tg://resolve?domain=AnKoVPN_bot";
                }
            };
        </script>
        <style>
            body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #0f172a; color: #fff; margin: 0; text-align: center; }
            .container { padding: 20px; }
            h2 { color: #10b981; }
            a { color: #3b82f6; text-decoration: none; border: 1px solid #3b82f6; padding: 10px 20px; border-radius: 8px; display: inline-block; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Оплата успешно завершена!</h2>
            <p>Сейчас вы будете перенаправлены обратно в Telegram-бота.</p>
            <p>Если перенаправление не произошло автоматически, нажмите кнопку ниже:</p>
            <a href="tg://resolve?domain=AnKoVPN_bot" target="_top">Открыть Telegram</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
