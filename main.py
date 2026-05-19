from datetime import datetime, timedelta, timezone

from contextlib import asynccontextmanager

from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.core import bot, dp
from app.core.config import settings
from app.db.database import async_session_maker, get_async_session
from app.db.models import Payment, User
from app.services.xray import XrayService
from app.services.yookassa import (
    fetch_remote_payment,
    is_valid_yookassa_webhook_auth,
    parse_yookassa_notification,
)

scheduler = AsyncIOScheduler(timezone="UTC")


async def activate_payment_by_id(session: AsyncSession, payment_id: str) -> bool:
    payment = await session.scalar(select(Payment).where(Payment.payment_id == payment_id))
    if payment is None:
        return False

    if payment.status == "success":
        return True

    user = await session.scalar(select(User).where(User.id == payment.user_id))
    if user is None:
        return False

    payment.status = "success"
    user.is_active = True

    now = datetime.now(timezone.utc)
    if user.sub_end_date is None or user.sub_end_date <= now:
        user.sub_end_date = now + timedelta(days=30)
    else:
        user.sub_end_date = user.sub_end_date + timedelta(days=30)

    await session.commit()
    await XrayService.add_client(email=str(user.telegram_id), uuid=user.vless_uuid)
    await bot.send_message(user.telegram_id, "Оплата получена. Доступ выдан.")
    return True


async def check_pending_payments() -> None:
    async with async_session_maker() as session:
        pending_payments = (
            await session.scalars(select(Payment).where(Payment.status == "pending"))
        ).all()

        for db_payment in pending_payments:
            remote_payment = await fetch_remote_payment(db_payment.payment_id)
            if remote_payment.status == "succeeded":
                await activate_payment_by_id(session, db_payment.payment_id)


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
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/webhook/telegram')
async def telegram_webhook(request: Request) -> dict[str, bool]:
    update_data = await request.json()
    update = Update.model_validate(update_data)
    await dp.feed_update(bot, update)
    return {'ok': True}


@app.post('/webhook/yookassa')
async def yookassa_webhook(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    if not is_valid_yookassa_webhook_auth(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")

    request_body = await request.json()
    notification = parse_yookassa_notification(request_body)
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}

    payment_id = notification.object.id
    is_processed = await activate_payment_by_id(session, payment_id)
    if not is_processed:
        return {"status": "not_found"}

    return {"status": "ok"}
