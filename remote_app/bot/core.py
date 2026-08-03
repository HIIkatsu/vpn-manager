from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.bot.handlers import router
from app.bot.middlewares.db_session import DbSessionMiddleware
from app.core.settings import settings

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.update.middleware(DbSessionMiddleware())
dp.include_router(router)
