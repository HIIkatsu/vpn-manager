from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.profile import get_profile_data
from app.bot.handlers.subscription import subscription_keyboard
from app.core.settings import settings
from app.services.user_service import UserService

router = Router()


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def stats_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.")
        return

    now = datetime.now(timezone.utc)
    created_at = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    days_in_service = max((now - created_at).days, 0)
    days_left = 0
    if user.sub_end_date:
        sub_end_date = user.sub_end_date if user.sub_end_date.tzinfo else user.sub_end_date.replace(tzinfo=timezone.utc)
        delta = sub_end_date - now
        days_left = max(delta.days, 0)

    await message.answer(
        "<b>Ваша статистика</b>\n"
        f"• Дней с нами: <b>{days_in_service}</b>\n"
        f"• Осталось дней подписки: <b>{days_left}</b>\n"
        f"• Статус: <b>{'Активна' if user.is_active else 'Неактивна'}</b>"
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
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

    now = datetime.now(timezone.utc)
    created_at = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    days_in_service = max((now - created_at).days, 0)
    days_left = 0
    if user.sub_end_date:
        sub_end_date = user.sub_end_date if user.sub_end_date.tzinfo else user.sub_end_date.replace(tzinfo=timezone.utc)
        delta = sub_end_date - now
        days_left = max(delta.days, 0)

    await callback.message.answer(
        "<b>Ваша статистика</b>\n"
        f"• Дней с нами: <b>{days_in_service}</b>\n"
        f"• Осталось дней подписки: <b>{days_left}</b>\n"
        f"• Статус: <b>{'Активна' if user.is_active else 'Неактивна'}</b>"
    )
    await callback.answer()


@router.callback_query(F.data == "menu_profile")
async def profile_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return

    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
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
