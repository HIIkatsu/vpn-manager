import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.bot.core import bot
from app.db.database import async_session_maker
from app.db.models import User
from app.services.xray_manager import XrayManager


async def run_auto_expiry_iteration() -> None:
    async with async_session_maker() as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        xray = XrayManager()

        expired_stmt = select(User).where(User.is_active.is_(True), User.sub_end_date < now)
        expired = (await session.execute(expired_stmt)).scalars().all()

        for user in expired:
            if await xray.remove_client(email=str(user.telegram_id)):
                user.is_active = False
                msg = (
                    "🔴 <b>Срок действия подписки завершён!</b>\n\n"
                    "Доступ к VPN ограничен. Мы сохраним ваши настройки "
                    "ещё на 7 дней. Вы можете продлить подписку в любой "
                    "момент через меню бота, чтобы доступ включился автоматически."
                )
                try:
                    await bot.send_message(chat_id=int(user.telegram_id), text=msg, parse_mode="HTML")
                except Exception:
                    pass

        deadline = now - timedelta(days=7)
        delete_stmt = select(User).where(User.is_active.is_(False), User.sub_end_date < deadline)
        to_delete = (await session.execute(delete_stmt)).scalars().all()

        for user in to_delete:
            await xray.remove_client(email=str(user.telegram_id))
            msg = (
                "🗑️ <b>Ваш профиль удален.</b>\n\n"
                "Вы не продлевали подписку более 7 дней. "
                "Конфигурация аннулирована. Для возвращения "
                "создайте профиль через команду /start."
            )
            try:
                await bot.send_message(chat_id=int(user.telegram_id), text=msg, parse_mode="HTML")
            except Exception:
                pass
            await session.delete(user)

        await session.commit()


async def auto_expiry_loop(interval_seconds: int = 1800) -> None:
    while True:
        try:
            await run_auto_expiry_iteration()
        except Exception as exc:
            print(f"Cron Error: {exc}")
        await asyncio.sleep(interval_seconds)
