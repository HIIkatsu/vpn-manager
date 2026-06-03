import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.bot.core import bot
from app.core.logging_utils import log_context
from app.db.database import async_session_maker
from app.db.models import OutboxEvent, SubscriptionNotification, User
from app.db.repositories.outbox_repo import OutboxRepository
from app.services.transaction import session_scope
from app.services.xray_manager import XrayManager
from app.services.user_lifecycle import delete_user_with_relations
from app.core.settings import settings
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, URLInputFile

logger = logging.getLogger(__name__)

# --- МИКРО-ТАСКА 1: МГНОВЕННАЯ ДОСТАВКА СОБЫТИЙ ---
async def outbox_loop(interval_seconds: int = 5) -> None:
    xray = XrayManager()
    while True:
        try:
            delivered, failed = 0, 0
            async with session_scope(async_session_maker) as session:
                repo = OutboxRepository(session)
                events = await repo.claim_pending_batch(limit=50)
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
                        repo.mark_processed(event)
                        delivered += 1
                    else:
                        repo.mark_failed(event, err or "error")
                        failed += 1
                        
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
                    users = (await session.execute(select(User).where(User.telegram_id.in_([int(k) for k in stats.keys() if str(k).isdigit()])))).scalars().all()
                    for user in users:
                        added_traffic = stats.get(str(user.telegram_id), 0)
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
            now = datetime.now(timezone.utc)
            async with async_session_maker() as session:
                expired_rows = (await session.execute(select(User).where(User.is_active.is_(True), User.sub_end_date < now))).scalars().all()
                expired_snapshots = [{"id": user.id, "telegram_id": user.telegram_id, "vless_uuid": user.vless_uuid} for user in expired_rows]
                
                deadline = now - timedelta(days=7)
                delete_rows = (await session.execute(select(User).where(User.is_active.is_(False), User.sub_end_date < deadline))).scalars().all()
                delete_snapshots = [{"id": user.id, "telegram_id": user.telegram_id, "vless_uuid": user.vless_uuid} for user in delete_rows]
                
            expired_results: dict[int, bool] = {}
            for user in expired_snapshots:
                removed = await xray.remove_client(email=str(user["telegram_id"]))
                expired_results[user["id"]] = removed
                if removed:
                    msg = "🔴 <b>Срок действия подписки завершён!</b>\n\nДоступ к VPN ограничен. Вы можете продлить подписку в любой момент через меню бота."
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Личный кабинет", url=f"https://neurosmmai.ru/cabinet/{user['vless_uuid']}")]])
                    try:
                        await bot.send_message(chat_id=int(user["telegram_id"]), text=msg, parse_mode="HTML", reply_markup=kb)
                    except Exception: pass
                        
            delete_results: dict[int, bool] = {}
            for user in delete_snapshots:
                delete_results[user["id"]] = await xray.remove_client(email=str(user["telegram_id"]))
                msg = "🗑️ <b>Ваш профиль удален.</b>\n\nВы не продлевали подписку более 7 дней. Для возвращения создайте профиль заново."
                try:
                    await bot.send_message(chat_id=int(user["telegram_id"]), text=msg, parse_mode="HTML")
                except Exception: pass
                        
            async with session_scope(async_session_maker) as session:
                for user_id, removed in expired_results.items():
                    if not removed: continue
                    user = await session.get(User, user_id)
                    if user: user.is_active = False
                for user_id in delete_results:
                    user = await session.get(User, user_id)
                    if user: await delete_user_with_relations(session, user)
        except Exception as exc:
            logger.exception("Expiry Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)

# --- МИКРО-ТАСКА 4: ИДЕМПОТЕНТНЫЕ УВЕДОМЛЕНИЯ ---
def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def _notification_type_for_hours_left(hours_left: float) -> str | None:
    if 48 < hours_left <= 72: return "3_days"
    if 12 < hours_left <= 24: return "1_day"
    if 0 < hours_left <= 12: return "0_days"
    return None

async def _claim_subscription_notification(user: dict, notify_type: str, now: datetime) -> int | None:
    async with async_session_maker() as session:
        try:
            marker = await session.scalar(select(SubscriptionNotification).where(
                SubscriptionNotification.user_id == user["id"],
                SubscriptionNotification.notify_type == notify_type,
                SubscriptionNotification.sub_end_date == user["sub_end_date"]
            ))
            stale_processing_cutoff = now - timedelta(minutes=10)
            
            if marker is None:
                marker = SubscriptionNotification(user_id=user["id"], notify_type=notify_type, sub_end_date=user["sub_end_date"], status="processing", locked_at=now)
                session.add(marker)
            elif marker.status == "sent": return None
            elif marker.status == "processing" and marker.locked_at and _ensure_aware(marker.locked_at) > stale_processing_cutoff: return None
            elif marker.retry_at and _ensure_aware(marker.retry_at) > now: return None
            else:
                marker.status = "processing"
                marker.locked_at = now
            await session.commit()
            return marker.id
        except IntegrityError:
            await session.rollback()
            return None

async def _finalize_subscription_notification(marker_id: int, *, sent: bool, error: str | None = None) -> None:
    async with async_session_maker() as session:
        marker = await session.get(SubscriptionNotification, marker_id)
        if marker is None: return
        now = datetime.now(timezone.utc)
        marker.locked_at = None
        if sent:
            marker.status = "sent"
            marker.sent_at = now
            marker.last_error = None
            marker.retry_at = None
        else:
            marker.status = "pending"
            marker.last_error = (error or "telegram send failed")[:4000]
            marker.retry_at = now + timedelta(minutes=30)
        await session.commit()

async def notification_loop(interval_seconds: int = 3600) -> None:
    IMAGE_URL = "https://telegra.ph/file/18a2872bc9dafb527a054.png"
    while True:
        try:
            now = datetime.now(timezone.utc)
            async with async_session_maker() as session:
                active_users = (await session.execute(select(User).where(User.is_active.is_(True), User.sub_end_date.is_not(None)))).scalars().all()
                snapshots = [{"id": u.id, "telegram_id": u.telegram_id, "vless_uuid": u.vless_uuid, "sub_end_date": _ensure_aware(u.sub_end_date)} for u in active_users]
                
            for user in snapshots:
                hours_left = (user["sub_end_date"] - now).total_seconds() / 3600
                if hours_left <= 0: continue
                
                notify_type = _notification_type_for_hours_left(hours_left)
                if not notify_type: continue
                
                marker_id = await _claim_subscription_notification(user, notify_type, now)
                if marker_id is None: continue
                
                try:
                    sub_id = str(user["telegram_id"])[-6:]
                    end_msk = (user["sub_end_date"] + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
                    d_left, h_left = int(hours_left // 24), int(hours_left % 24)
                    time_str = f"{d_left} дн. {h_left} ч." if d_left > 0 else f"{h_left} ч."
                    
                    msg = (f"⏰ <b>Подписка #{sub_id} почти закончилась</b>\n"
                           f"Осталось совсем немного — <b>{time_str}</b>\n"
                           f"📅 Дата отключения: <code>{end_msk} (МСК)</code>\n"
                           f"Позаботьтесь об этом заранее — продлите подписку, и интернет будет работать без пауз 💙")
                           
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Оплатить подписку", url="https://t.me/ankovpn_bot?start=pay")]])
                    
                    try:
                        await bot.send_photo(chat_id=int(user["telegram_id"]), photo=URLInputFile(IMAGE_URL), caption=msg, parse_mode="HTML", reply_markup=kb)
                    except Exception:
                        await bot.send_message(chat_id=int(user["telegram_id"]), text=msg, parse_mode="HTML", reply_markup=kb)
                        
                    await _finalize_subscription_notification(marker_id, sent=True)
                except Exception as exc:
                    await _finalize_subscription_notification(marker_id, sent=False, error=str(exc))
        except Exception as exc:
            logger.exception("Notification Loop Error: %s", exc)
        await asyncio.sleep(interval_seconds)
