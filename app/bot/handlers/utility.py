from datetime import datetime, timezone
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from app.bot.handlers.profile import get_profile_data
from app.bot.handlers.subscription import subscription_keyboard
from app.core.settings import settings
from app.services.user_service import UserService
from app.services.xray_manager import XrayManager

router = Router()

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} TB"

def make_progress_bar(percent: float, length: int = 12) -> str:
    filled = int((percent / 100.0) * length)
    empty = length - filled
    return "█" * filled + "░" * empty

def build_stats_text(user, used_bytes: int = 0) -> str:
    now = datetime.now(timezone.utc)
    created_at = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    days_in_service = max((now.date() - created_at.date()).days, 0)
    
    sub_end_date = None
    days_left = 0
    if user.sub_end_date:
        sub_end_date = user.sub_end_date if user.sub_end_date.tzinfo else user.sub_end_date.replace(tzinfo=timezone.utc)
        days_left = max((sub_end_date.date() - now.date()).days, 0)
        
    sub_end_human = sub_end_date.strftime("%d.%m.%Y") if sub_end_date else "—"
    
    percent = round(min(100.0, (used_bytes / (1024**4)) * 100), 2)
    traffic_str = format_bytes(used_bytes)
    bar = make_progress_bar(percent)
    
    return (
        "<b>Ваша статистика</b>\n"
        f"• Дней с нами: <b>{days_in_service}</b>\n"
        f"• Дата регистрации: <b>{created_at.strftime('%d.%m.%Y')}</b>\n"
        f"• Осталось дней подписки: <b>{days_left}</b>\n"
        f"• Подписка до: <b>{sub_end_human}</b>\n"
        f"• Статус: <b>{'Активна' if user.is_active else 'Неактивна'}</b>\n\n"
        f"📊 <b>Расход трафика:</b>\n"
        f"<code>[{bar}]</code> {percent}%\n"
        f"Израсходовано: <b>{traffic_str}</b> из 1 TB\n"
        f"<i>* 1 TB не является ограничением и нужен лишь для удобного отображения расхода.</i>"
    )

async def _get_user_traffic(telegram_id: int) -> int:
    try:
        xray = XrayManager()
        stats = await xray.get_live_traffic_stats()
        return stats.get(str(telegram_id), 0)
    except Exception:
        return 0

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def stats_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.")
        return
    used_bytes = await _get_user_traffic(user.telegram_id)
    await message.answer(build_stats_text(user, used_bytes))

@router.message(Command("help"))
@router.message(F.text.in_({"❓ Помощь", "❓  Помощь"}))
async def help_handler(message: Message) -> None:
    await message.answer(
        "<b>Помощь</b>\n"
        "• Нажмите 🚀 Подключить VPN для быстрого старта.\n"
        "• В разделе 👤 Профиль находится персональная ссылка подписки.\n"
        "• В разделе 💳 Подписка можно продлить доступ."
    )

@router.callback_query(F.data == "menu_help")
async def help_callback(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "<b>Помощь</b>\n"
        "• Нажмите 🚀 Подключить VPN для быстрого старта.\n"
        "• В разделе 👤 Профиль находится персональная ссылка подписки.\n"
        "• В разделе 💳 Подписка можно продлить доступ."
    )
    await callback.answer()

@router.callback_query(F.data == "menu_stats")
async def stats_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return
    used_bytes = await _get_user_traffic(user.telegram_id)
    await callback.message.answer(build_stats_text(user, used_bytes))
    await callback.answer()

@router.callback_query(F.data == "menu_profile")
async def profile_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return
    used_bytes = await _get_user_traffic(user.telegram_id)
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, format_bytes(used_bytes))
    await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "menu_subscription")
async def subscription_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return
    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y %H:%M UTC") if user.sub_end_date else "Не оформлена"
    await callback.message.answer(
        f"<b>Подписка</b>\n"
        f"Статус: {status}\n"
        f"Действует до: <code>{sub_end}</code>\n\n"
        "Продление добавляет 30 дней доступа.",
        reply_markup=subscription_keyboard(),
    )
    await callback.answer()
