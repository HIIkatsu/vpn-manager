from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.bot.middlewares import DbSessionMiddleware
from aiogram.client.default import DefaultBotProperties

from app.core.config import settings


bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()

dp.update.middleware(DbSessionMiddleware())
dp.include_router(router)
