import hashlib, hmac, json, logging
from datetime import datetime as dt, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, Form, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.dependencies.common import get_write_session
from app.bot.core import bot
from app.db.models import User
from app.db.repositories.outbox_repo import OutboxRepository

router = APIRouter()
logger = logging.getLogger(__name__)

def get_env(key, default=""):
    try:
        with open("/root/vpn-manager-v2/.env", "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default

async def activate_subscription(session: AsyncSession, telegram_id: int, days: int, payment_source: str, tx_id: str):
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user: return False
    now = dt.now(timezone.utc)
    if user.sub_end_date is None or user.sub_end_date < now: user.sub_end_date = now + timedelta(days=days)
    else: user.sub_end_date += timedelta(days=days)
    user.is_active = True
    
    outbox = OutboxRepository(session)
    await outbox.enqueue(event_type="xray.add_client", aggregate_type=f"{payment_source}_payment", aggregate_id=str(user.id), dedup_key=f"xray.add_client:pay_{tx_id}", payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid}))
    await session.commit()
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="menu_profile")]])
        await bot.send_message(chat_id=telegram_id, text=f"✅ <b>Оплата успешно получена!</b>\n\nВам начислено <b>+{days} дней</b> премиум-доступа.", parse_mode="HTML", reply_markup=kb)
    except Exception: pass
    return True

@router.post("/api/yoomoney/webhook")
async def yoomoney_webhook(request: Request, notification_type: str = Form(...), operation_id: str = Form(...), amount: str = Form(...), currency: str = Form(...), datetime_str: str = Form(..., alias="datetime"), sender: str = Form(""), codepro: str = Form(...), label: str = Form(""), sha1_hash: str = Form(...), session: AsyncSession = Depends(get_write_session)):
    YOOMONEY_SECRET = get_env("YOOMONEY_SECRET", "")
    hash_string = f"{notification_type}&{operation_id}&{amount}&{currency}&{datetime_str}&{sender}&{codepro}&{YOOMONEY_SECRET}&{label}"
    if hashlib.sha1(hash_string.encode('utf-8')).hexdigest() != sha1_hash: return Response(status_code=400, content="Invalid hash")
    if not label or "_" not in label: return Response(status_code=200, content="Ignored")
    try:
        tg_str, days_str = label.split("_")
        await activate_subscription(session, int(tg_str), int(days_str), "yoomoney", operation_id)
    except Exception: pass
    return Response(status_code=200, content="OK")

@router.post("/api/payments/anypay-webhook")
async def anypay_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    form_data = await request.form()
    merchant_id = form_data.get("merchant_id")
    amount = form_data.get("amount")
    pay_id = form_data.get("pay_id", "")
    status_pay = form_data.get("status")
    received_sign = form_data.get("sign")
    transaction_id = form_data.get("transaction_id", "unknown")
    currency = form_data.get("currency", "RUB")
    
    if not all([merchant_id, amount, pay_id, received_sign]):
        return Response(content="ERROR: Missing parameters", status_code=400)

    secret = get_env("ANYPAY_SECRET_KEY", "")

    # ОФИЦИАЛЬНЫЙ алгоритм AnyPay для вебхуков
    sign_str_official = f"{currency}:{amount}:{pay_id}:{merchant_id}:{status_pay}:{secret}"
    calc_sign_official = hashlib.sha256(sign_str_official.encode('utf-8')).hexdigest()

    # Твой СТАРЫЙ алгоритм (на случай если у тебя legacy-настройки в кассе)
    sign_str_fallback = f"{merchant_id}:{amount}:{pay_id}:{secret}"
    calc_sign_fallback = hashlib.sha256(sign_str_fallback.encode('utf-8')).hexdigest()

    if received_sign not in [calc_sign_official, calc_sign_fallback]:
        debug_msg = f"ERROR. We tried official: '{sign_str_official}' and fallback: '{sign_str_fallback}'. Secret is {len(secret)} chars."
        logger.error(f"[ANYPAY] {debug_msg} Got: {received_sign}")
        return Response(content=debug_msg, status_code=403)
            
    if status_pay != "paid":
        return Response(content="OK", status_code=200)

    try:
        pay_id_str = str(pay_id)
        if len(pay_id_str) <= 11:
            tg_id = int(pay_id_str)
        else:
            tg_id = int(pay_id_str[:-3])
            
        amt = float(amount)
        days = 365 if amt >= 900 else 90 if amt >= 250 else 30
        await activate_subscription(session, tg_id, days, "anypay", transaction_id)
    except Exception as e:
        logger.error(f"AnyPay Webhook Error: {e}")

    return Response(content="OK", status_code=200)

@router.post("/api/cryptobot/webhook")
async def cryptobot_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not signature: return Response(status_code=400, content="Missing signature")
    CRYPTOBOT_TOKEN = get_env("CRYPTOBOT_TOKEN", "")
    secret = hashlib.sha256(CRYPTOBOT_TOKEN.encode('utf-8')).digest()
    calc_signature = hmac.new(secret, body, digestmod=hashlib.sha256).hexdigest()
    if calc_signature != signature: return Response(status_code=403, content="Invalid signature")
    try:
        data = json.loads(body)
        if data.get("update_type") == "invoice_paid":
            payload_str = data["payload"]["payload"]
            invoice_id = str(data["payload"]["invoice_id"])
            parts = payload_str.split("_")
            if len(parts) >= 2: await activate_subscription(session, int(parts[0]), int(parts[1]), "crypto", invoice_id)
    except Exception: pass
    return Response(status_code=200, content="OK")
