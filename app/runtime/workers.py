import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.bot.core import bot
from app.core.logging_utils import log_context
from app.db.database import async_session_maker
from app.db.models import OutboxEvent, User
from app.db.repositories.outbox_repo import OutboxRepository
from app.services.transaction import session_scope
from app.services.xray_manager import XrayManager

logger = logging.getLogger(__name__)


async def run_outbox_delivery_iteration(batch_size: int = 100) -> tuple[int, int]:
    """Transaction boundaries:
    1) lock and snapshot pending outbox events in a short transaction;
    2) call external Xray API outside transaction;
    3) persist results in a short transaction.
    """
    xray = XrayManager()
    delivered = 0
    failed = 0

    async with session_scope(async_session_maker) as session:
        repo = OutboxRepository(session)
        events = await repo.get_pending_batch(limit=batch_size)
        event_snapshots = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload_json": event.payload_json,
            }
            for event in events
        ]

    outcomes: dict[int, tuple[bool, str | None]] = {}
    for event in event_snapshots:
        try:
            payload = json.loads(event["payload_json"])
            if event["event_type"] == "xray.add_client":
                ok = await xray.add_client(email=str(payload["telegram_id"]), uuid=payload["uuid"])
            else:
                ok = False
            outcomes[event["id"]] = (ok, None if ok else "xray call returned false")
        except Exception as exc:
            outcomes[event["id"]] = (False, str(exc))

    async with session_scope(async_session_maker) as session:
        repo = OutboxRepository(session)
        db_events = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.id.in_(list(outcomes.keys()))))
        ).scalars().all()
        by_id = {event.id: event for event in db_events}

        for event_id, (ok, err) in outcomes.items():
            event = by_id.get(event_id)
            if event is None:
                continue
            if ok:
                repo.mark_processed(event)
                delivered += 1
            else:
                repo.mark_failed(event, err or "unknown error")
                failed += 1

    return delivered, failed


async def run_xray_reconciliation_iteration() -> tuple[int, int]:
    """Transaction boundaries:
    1) fetch candidate users in short transaction;
    2) external Xray calls outside transaction;
    3) persist status changes in short transaction.
    """
    xray = XrayManager()
    now = datetime.now(timezone.utc)
    activated = 0
    deactivated = 0

    async with session_scope(async_session_maker) as session:
        should_be_active = (
            await session.execute(select(User).where(User.sub_end_date.is_not(None), User.sub_end_date > now))
        ).scalars().all()
        should_be_inactive = (
            await session.execute(select(User).where((User.sub_end_date.is_(None)) | (User.sub_end_date <= now)))
        ).scalars().all()
        active_targets = [
            {"id": user.id, "telegram_id": user.telegram_id, "vless_uuid": user.vless_uuid, "is_active": user.is_active}
            for user in should_be_active
        ]
        inactive_targets = [
            {"id": user.id, "telegram_id": user.telegram_id, "is_active": user.is_active}
            for user in should_be_inactive
        ]

    activate_ids: list[int] = []
    deactivate_ids: list[int] = []

    for user in active_targets:
        ok = await xray.add_client(email=str(user["telegram_id"]), uuid=user["vless_uuid"])
        if ok and not user["is_active"]:
            activate_ids.append(user["id"])

    for user in inactive_targets:
        ok = await xray.remove_client(email=str(user["telegram_id"]))
        if ok and user["is_active"]:
            deactivate_ids.append(user["id"])

    async with session_scope(async_session_maker) as session:
        if activate_ids:
            to_activate = (await session.execute(select(User).where(User.id.in_(activate_ids)))).scalars().all()
            for user in to_activate:
                user.is_active = True
            activated = len(to_activate)

        if deactivate_ids:
            to_deactivate = (await session.execute(select(User).where(User.id.in_(deactivate_ids)))).scalars().all()
            for user in to_deactivate:
                user.is_active = False
            deactivated = len(to_deactivate)

    return activated, deactivated


async def run_auto_expiry_iteration() -> None:
    async with session_scope(async_session_maker) as session:
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
                    logger.exception(
                        "Failed to send expiry notification",
                        extra=log_context(telegram_id=user.telegram_id, action_source="auto_expiry_notify"),
                    )

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
                logger.exception(
                    "Failed to send profile deletion notification",
                    extra=log_context(telegram_id=user.telegram_id, action_source="auto_expiry_delete_notify"),
                )
            await session.delete(user)


async def auto_expiry_loop(interval_seconds: int = 1800) -> None:
    while True:
        try:
            await run_auto_expiry_iteration()
            delivered, failed = await run_outbox_delivery_iteration()
            if failed:
                logger.warning(
                    "Outbox delivery failures",
                    extra=log_context(
                        action_source="outbox_delivery",
                        event_id="batch",
                        endpoint="xray.add_client",
                    )
                    | {"failed": failed, "delivered": delivered},
                )
            await run_xray_reconciliation_iteration()
        except Exception as exc:
            logger.exception(
                "Cron Error: %s",
                exc,
                extra=log_context(action_source="workers.auto_expiry_loop"),
            )
        await asyncio.sleep(interval_seconds)
