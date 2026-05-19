from contextlib import asynccontextmanager

from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.core import bot, dp
from app.core.container import get_async_session, get_billing_service
from app.core.settings import settings
from app.db.database import async_session_maker
from app.services.billing_service import BillingService
from app.services.yookassa_service import YooKassaService

scheduler = AsyncIOScheduler(timezone="UTC")


async def check_pending_payments() -> None:
    async with async_session_maker() as session:
        billing = get_billing_service(session)
        await billing.process_pending()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.set_webhook(url=settings.WEBHOOK_URL, secret_token=settings.WEBHOOK_SECRET)
    scheduler.add_job(check_pending_payments, "interval", seconds=30, id="pending_payments_check")
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


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_async_session)) -> dict[str, str]:
    yookassa = YooKassaService()
    if not yookassa.is_valid_webhook_auth(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")

    notification = yookassa.parse_notification(await request.json())
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}

    billing: BillingService = get_billing_service(session)
    if not await billing.activate_payment(notification.object.id):
        return {"status": "not_found"}
    return {"status": "ok"}
