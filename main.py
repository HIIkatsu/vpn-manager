from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.bot.core import bot, dp
from app.core.config import settings


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
