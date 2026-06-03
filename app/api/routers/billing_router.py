import hashlib, hmac, json, logging
from datetime import datetime as dt, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.common import get_write_session
from app.bot.core import bot
from app.db.models import User, OutboxEvent, Payment
from app.db.repositories.outbox_repo import OutboxRepository
from app.services.yookassa_service import YooKassaService
from app.core.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

async def activate_subscription(session: AsyncSession, telegram_id: int, days: int, payment_source: str, tx_id: str):
    dedup_key = f"xray.add_client:pay_{payment_source}_{tx_id}"
    
    # Идемпотентность БД (Защита от дублей)
    if await session.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key)):
        return True

    # Блокировка от гонки потоков (Race Condition)
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
    if not user: return False
    
    now = dt.now(timezone.utc)
    if user.sub_end_date is None or user.sub_end_date < now: user.sub_end_date = now + timedelta(days=days)
    else: user.sub_end_date += timedelta(days=days)
    user.is_active = True
    
    outbox = OutboxRepository(session)
    await outbox.enqueue(event_type="xray.add_client", aggregate_type=f"{payment_source}_payment", aggregate_id=str(user.id), dedup_key=dedup_key, payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid}))
    await session.commit()
    
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="menu_profile")]])
        await bot.send_message(chat_id=telegram_id, text=f"✅ <b>Оплата успешно получена!</b>\n\nВам начислено <b>+{days} дней</b> премиум-доступа.", parse_mode="HTML", reply_markup=kb)
    except Exception: pass
    return True

@router.post("/api/yookassa/webhook")
@router.post("/api/yookassa/webhook/")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    yk_service = YooKassaService()
    try:
        payload = await request.json()
        notification = yk_service.parse_notification(payload)
        
        if notification and notification.event == "payment.succeeded":
            payment_id = notification.object.id
            payment = await session.scalar(select(Payment).where(Payment.payment_id == payment_id))
            
            if payment and payment.status == "success": 
                return Response(status_code=200, content="OK")
            if payment: 
                payment.status = "success"
                
            user_id = notification.object.metadata.get("user_id")
            if user_id:
                user = await session.get(User, int(user_id))
                if user:
                    amt = float(notification.object.amount.value)
                    days = 365 if amt >= 900.0 else 90 if amt >= 250.0 else 30
                    await activate_subscription(session, user.telegram_id, days, "yookassa", payment_id)
                    
    except Exception as e: logger.error(f"YooKassa Webhook Error: {e}")
    # Всегда отвечаем 200, чтобы ЮКасса не дублировала хуки и не отключала вебхук
    return Response(status_code=200, content="OK")

@router.post("/api/payments/anypay-webhook")
async def anypay_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    form_data = await request.form()
    merchant_id, amount, pay_id, status_pay, received_sign = form_data.get("merchant_id"), form_data.get("amount"), form_data.get("pay_id", ""), form_data.get("status"), form_data.get("sign")
    transaction_id, currency = form_data.get("transaction_id", "unknown"), form_data.get("currency", "RUB")
    
    if not all([merchant_id, amount, pay_id, received_sign]): return Response(content="ERROR", status_code=400)
    
    secret = getattr(settings, "ANYPAY_SECRET_KEY", "")
    valid_signs = [
        hashlib.sha256(f"{currency}:{amount}:{pay_id}:{merchant_id}:{status_pay}:{secret}".encode()).hexdigest(),
        hashlib.sha256(f"{merchant_id}:{amount}:{pay_id}:{secret}".encode()).hexdigest()
    ]
    if received_sign not in valid_signs: return Response(content="Forbidden", status_code=403)
    if status_pay != "paid": return Response(content="OK", status_code=200)
    
    try:
        tg_id = int(str(pay_id)) if len(str(pay_id)) <= 11 else int(str(pay_id)[:-3])
        days = 365 if float(amount) >= 900 else 90 if float(amount) >= 250 else 30
        await activate_subscription(session, tg_id, days, "anypay", transaction_id)
    except Exception: pass
    return Response(content="OK", status_code=200)

@router.post("/api/cryptobot/webhook")
async def cryptobot_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not signature: return Response(status_code=400, content="Missing signature")
    
    secret = hashlib.sha256(getattr(settings, "CRYPTOBOT_TOKEN", "").encode()).digest()
    if hmac.new(secret, body, digestmod=hashlib.sha256).hexdigest() != signature: return Response(status_code=403, content="Invalid signature")
    
    try:
        data = json.loads(body)
        if data.get("update_type") == "invoice_paid":
            parts = data["payload"]["payload"].split("_")
            if len(parts) >= 2: 
                await activate_subscription(session, int(parts[0]), int(parts[1]), "crypto", str(data["payload"]["invoice_id"]))
    except Exception: pass
    return Response(status_code=200, content="OK")
