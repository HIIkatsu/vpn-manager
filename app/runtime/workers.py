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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# --- МИКРО-ТАСКА 1: МГНОВЕННАЯ ДОСТАВКА СОБЫТИЙ ---
async def outbox_loop(interval_seconds: int = 5) -> None:
    xray = XrayManager()
    while True:
        try:
            delivered, failed = 0, 0
            async with session_scope(async_session_maker) as session:
                repo = OutboxRepository(session)
                events = await repo.get_pending_batch(limit=50)
                event_snapshots = [{"id": e.id, "event_type": e.event_type, "payload_json": e.payload_json} for e in events]
            if not event_snapshots:
                await asyncio.sleep(interval_seconds)
                continue
            outcomes = {}
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
                db_events = (await session.execute(select(OutboxEvent).where(OutboxEvent.id.in_(list(outcomes.keys()))))).scalars().all()
                by_id = {e.id: e for e in db_events}
                for event_id, (ok, err) in outcomes.items():
                    event = by_id.get(event_id)
                    if not event: continue
                    if ok:
                        repo.mark_processed(event); delivered += 1
                    else:
                        repo.mark_failed(event, err or "error"); failed += 1
            if failed: logger.warning(f"Outbox delivery: {failed} failed, {delivered} delivered")
        except Exception as exc:
            logger.exception("Outbox Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)

# --- МИКРО-ТАСКА 2: СБОР СТАТИСТИКИ ТРАФИКА ---
async def traffic_stats_loop(interval_seconds: int = 60) -> None:
    xray = XrayManager()
    while True:
        try:
            stats = await xray.get_live_traffic_stats(reset=True)
            if stats:
                async with session_scope(async_session_maker) as session:
                    users = (await session.execute(select(User).where(User.vless_uuid.in_(list(stats.keys()))))).scalars().all()
                    for user in users:
                        added_traffic = stats.get(user.vless_uuid, 0)
                        if added_traffic > 0:
                            user.traffic_total_bytes = (user.traffic_total_bytes or 0) + added_traffic
        except Exception as exc:
            logger.exception("Traffic Stats Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)

# --- МИКРО-ТАСКА 3: ОЧИСТКА И УДАЛЕНИЕ ИСТЕКШИХ ---
async def expiry_loop(interval_seconds: int = 900) -> None:
    xray = XrayManager()
    while True:
        try:
            async with session_scope(async_session_maker) as session:
                now = datetime.now(timezone.utc)
                expired = (await session.execute(select(User).where(User.is_active.is_(True), User.sub_end_date < now))).scalars().all()
                for user in expired:
                    if await xray.remove_client(email=str(user.telegram_id)):
                        user.is_active = False
                        msg = (
                            f"🔴 <b>Срок действия подписки завершён!</b>\n\n"
                            f"Доступ к VPN ограничен. Вы можете продлить подписку в любой момент через меню бота."
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Личный кабинет", url=f"https://neurosmmai.ru/cabinet/{user.vless_uuid}")]])
                        try: await bot.send_message(chat_id=int(user.telegram_id), text=msg, parse_mode="HTML", reply_markup=kb)
                        except Exception: pass
                
                deadline = now - timedelta(days=7)
                to_delete = (await session.execute(select(User).where(User.is_active.is_(False), User.sub_end_date < deadline))).scalars().all()
                for user in to_delete:
                    await xray.remove_client(email=str(user.telegram_id))
                    msg = "🗑️ <b>Ваш профиль удален.</b>\n\nВы не продлевали подписку более 7 дней. Для возвращения создайте профиль заново."
                    try: await bot.send_message(chat_id=int(user.telegram_id), text=msg, parse_mode="HTML")
                    except Exception: pass
                    await session.delete(user)
        except Exception as exc:
            logger.exception("Expiry Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)

# --- МИКРО-ТАСКА 4: КРАСИВЫЕ УВЕДОМЛЕНИЯ (Раз в час) ---
async def notification_loop(interval_seconds: int = 3600) -> None:
    notified_state = {}
    while True:
        try:
            async with session_scope(async_session_maker) as session:
                now = datetime.now(timezone.utc)
                active_users = (await session.execute(
                    select(User).where(User.is_active.is_(True), User.sub_end_date.is_not(None))
                )).scalars().all()

                for user in active_users:
                    user_end = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
                    delta = user_end - now
                    hours_left = delta.total_seconds() / 3600

                    if hours_left <= 0: continue

                    notify_type = None
                    if 48 < hours_left <= 72: notify_type = "3_days"
                    elif 12 < hours_left <= 24: notify_type = "1_day"
                    elif 0 < hours_left <= 12: notify_type = "0_days"

                    if notify_type:
                        state_key = f"{user.telegram_id}_{notify_type}_{user.sub_end_date.strftime('%Y%m%d')}"
                        if state_key not in notified_state:
                            try:
                                uuid_short = str(user.vless_uuid)[:8]
                                end_msk = (user.sub_end_date + timedelta(hours=3)).strftime("%d.%m.%Y, %H:%M")
                                d_left = int(hours_left // 24)
                                h_left = int(hours_left % 24)
                                time_str = f"{d_left} дн. {h_left} ч." if d_left > 0 else f"{h_left} ч."
                                
                                msg = (
                                    f"<b>Уведомление по подписке <code>{uuid_short}</code>:</b>\n\n"
                                    f"⚠️ <b>Ваш тариф скоро закончится.</b>\n"
                                    f"Выберите актуальный тариф, чтобы продолжить использование сервиса.\n\n"
                                    f"<b>Статус подписки:</b>\n"
                                    f"<blockquote>⏳ Осталось времени: {time_str}\n"
                                    f"📅 Дата окончания: {end_msk} (МСК)</blockquote>\n\n"
                                    f"<i>Успейте продлить выгодно, при истечении подписки доступ прекратится 👇</i>"
                                )
                                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Личный кабинет", url=f"https://neurosmmai.ru/cabinet/{user.vless_uuid}")]])
                                await bot.send_message(chat_id=int(user.telegram_id), text=msg, parse_mode="HTML", reply_markup=kb)
                                notified_state[state_key] = True
                            except Exception:
                                pass 

            if len(notified_state) > 5000: notified_state.clear()
        except Exception as exc:
            logger.exception("Notification Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)
