import asyncio
from app.core.settings import settings
from aiogram import Bot

async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    info = await bot.get_webhook_info()
    print("-" * 40)
    print(f"Текущий URL вебхука: {info.url}")
    print(f"Очередь сообщений: {info.pending_update_count}")
    print(f"Последняя ошибка: {info.last_error_message or 'Нет ошибок'}")
    print(f"Дата ошибки: {info.last_error_date or '—'}")
    print("-" * 40)
    await bot.session.close()

asyncio.run(main())
