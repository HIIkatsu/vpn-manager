from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.core.config import settings


bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
