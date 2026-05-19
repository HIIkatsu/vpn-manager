from datetime import datetime, timedelta, timezone

from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yookassa.domain.notification import WebhookNotificationFactory

from app.bot.core import bot, dp
from app.core.config import settings
from app.db.database import get_async_session
from app.db.models import Payment, User
from app.services.xray import XrayService


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.set_webhook(url=settings.WEBHOOK_URL, secret_token=settings.WEBHOOK_SECRET)
    try:
        yield
    finally:
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
    try:
        request_body = await request.json()
        notification = WebhookNotificationFactory().create(request_body)
    except Exception:
        return {"status": "ignored"}

    if notification.event != "payment.succeeded":
        return {"status": "ignored"}

    payment_id = notification.object.id
    user_id = int(notification.object.metadata.get("user_id", 0))
    if user_id == 0:
        return {"ok": True}

    payment = await session.scalar(select(Payment).where(Payment.payment_id == payment_id))
    user = await session.scalar(select(User).where(User.id == user_id))

    if payment is None or user is None:
        return {"status": "not_found"}

    payment.status = "success"
    user.is_active = True

    now = datetime.now(timezone.utc)
    if user.sub_end_date is None or user.sub_end_date <= now:
        user.sub_end_date = now + timedelta(days=30)
    else:
        user.sub_end_date = user.sub_end_date + timedelta(days=30)

    await session.commit()
    await XrayService.add_client(email=str(user.telegram_id), uuid=user.vless_uuid)
    await bot.send_message(user.telegram_id, "Ваша подписка успешно продлена!")

    return {"status": "ok"}
