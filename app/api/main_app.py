import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from app.bot.core import bot, dp
from app.core.container import get_async_session, get_billing_service
from app.core.settings import settings
from app.db.database import async_session_maker
from app.services.billing_service import BillingService
from app.services.yookassa_service import YooKassaService
from app.services.xray_manager import XrayManager

scheduler = AsyncIOScheduler(timezone="UTC")

async def check_pending_payments() -> None:
    async with async_session_maker() as session:
        billing = get_billing_service(session)
        await billing.process_pending()

async def check_expiring_subscriptions() -> None:
    async with async_session_maker() as session:
        billing = get_billing_service(session)
        await billing.notify_expiring_subscriptions(days_before=3)

@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.set_webhook(url=settings.WEBHOOK_URL, secret_token=settings.WEBHOOK_SECRET)
    scheduler.add_job(check_pending_payments, "interval", seconds=30, id="pending_payments_check")
    scheduler.add_job(check_expiring_subscriptions, "cron", hour=9, minute=0, id="expiring_subscriptions_check")
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/webhook/sub/{user_uuid}")
async def get_subscription(
    user_uuid: str,
    session: AsyncSession = Depends(get_async_session),
):
    billing = get_billing_service(session)
    user = await billing.users.get_by_vless_uuid(user_uuid)
    if user is None or not user.is_active or user.sub_end_date is None or user.sub_end_date <= datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Subscription inactive")
    xray = XrayManager()
    base_link = xray.generate_vless_link(user_uuid)
    clean_link = base_link.split("#")[0]
    direct = f"{clean_link}#%F0%9F%87%AB%F0%9F%87%AE%20Direct"
    smart = f"{clean_link}#%F0%9F%87%BA%F0%9F%87%B8%20USA%20warp"
    try:
        uuid_part, rest = clean_link.split("@", 1)
        domain_port, query = rest.split("?", 1)
        port = domain_port.split(":")[1] if ":" in domain_port else "443"
        emergency = f"{uuid_part}@84.252.75.36:{port}?{query}#%F0%9F%87%B7%F0%9F%87%BA%20%D0%A7%D0%B5%D0%B1%D1%83%D1%80%D0%BD%D0%B5%D1%82%20%28FirstByte%20L4%29"
    except Exception:
        emergency = f"{clean_link}#%F0%9F%87%B7%F0%9F%87%BA%20Emergency"
    bundle = f"{direct}\n{smart}\n{emergency}\n"
    encoded = base64.b64encode(bundle.encode('utf-8')).decode('utf-8')
    return Response(content=encoded, media_type="text/plain", headers={"Profile-Title": "🔥AnKo VPN".encode("utf-8").decode("latin-1")})

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_async_session)) -> dict[str, str]:
    yookassa = YooKassaService()
    notification = yookassa.parse_notification(await request.json())
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}
    billing: BillingService = get_billing_service(session)
    payment = await billing.payments.get_by_payment_id(notification.object.id)
    if payment is None:
        return {"status": "not_found"}
    if not await billing.activate_payment(notification.object.id):
        return {"status": "not_found"}
    try:
        from app.bot.core import bot
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="open_profile")]
        ])
        user = await session.get(__import__("app.db.models", fromlist=["User"]).User, payment.user_id)
        await bot.send_message(
            chat_id=user.telegram_id,
            text="✅ <b>Оплата успешно получена!</b>\nВаша подписка активирована.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Failed to send message: {e}")
    return {"status": "ok"}
