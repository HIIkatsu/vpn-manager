import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.bot.core import bot
from app.db.database import async_session_maker
from app.db.models import OutboxEvent, User
from app.db.repositories.outbox_repo import OutboxRepository
from app.services.xray_manager import XrayManager

logger = logging.getLogger(__name__)


async def run_outbox_delivery_iteration(batch_size: int = 100) -> tuple[int, int]:
    async with async_session_maker() as session:
        repo = OutboxRepository(session)
        xray = XrayManager()
        delivered = 0
        failed = 0

        events = await repo.get_pending_batch(limit=batch_size)
        for event in events:
            try:
                payload = json.loads(event.payload_json)
                if event.event_type == "xray.add_client":
                    ok = await xray.add_client(email=str(payload["telegram_id"]), uuid=payload["uuid"])
                else:
                    ok = False
                if ok:
                    repo.mark_processed(event)
                    delivered += 1
                else:
                    repo.mark_failed(event, "xray call returned false")
                    failed += 1
            except Exception as exc:
                repo.mark_failed(event, str(exc))
                failed += 1

        await session.commit()
        return delivered, failed


async def run_xray_reconciliation_iteration() -> tuple[int, int]:
    async with async_session_maker() as session:
        xray = XrayManager()
        now = datetime.now(timezone.utc)
        activated = 0
        deactivated = 0

        should_be_active = (await session.execute(select(User).where(User.sub_end_date.is_not(None), User.sub_end_date > now))).scalars().all()
        should_be_inactive = (await session.execute(select(User).where((User.sub_end_date.is_(None)) | (User.sub_end_date <= now)))).scalars().all()

        for user in should_be_active:
            ok = await xray.add_client(email=str(user.telegram_id), uuid=user.vless_uuid)
            if ok and not user.is_active:
                user.is_active = True
                activated += 1

        for user in should_be_inactive:
            ok = await xray.remove_client(email=str(user.telegram_id))
            if ok and user.is_active:
                user.is_active = False
                deactivated += 1

        await session.commit()
        return activated, deactivated


async def run_auto_expiry_iteration() -> None:
    async with async_session_maker() as session:
        now = datetime.now(timezone.utc)
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


async def auto_expiry_loop(interval_seconds: int = 1800) -> None:
    while True:
        try:
            await run_auto_expiry_iteration()
            delivered, failed = await run_outbox_delivery_iteration()
            if failed:
                logger.warning("Outbox delivery failures", extra={"failed": failed, "delivered": delivered})
            await run_xray_reconciliation_iteration()
        except Exception as exc:
            logger.exception("Cron Error: %s", exc)
        await asyncio.sleep(interval_seconds)
