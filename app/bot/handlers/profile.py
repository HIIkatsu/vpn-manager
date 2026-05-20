from aiogram import F, Router

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from app.bot.keyboards.main import main_keyboard
from app.services.user_service import UserService
from app.core.settings import settings

router = Router()

def get_profile_data(user, webhook_domain: str):
    if user.sub_end_date:
        sub_end_date = user.sub_end_date.strftime("%d.%m.%Y в %H:%M")
        status_emoji = "🟢" if user.is_active else "🔴"
        status_text = "Активна" if user.is_active else "Истекла"
    else:
        sub_end_date = "Не оформлена"
        status_emoji = "⚪"
        status_text = "Нет подписки"

    line = "━━━━━━━━━━━━━━━━━━━━━━━━"
    
    profile_text = (
        f"<b>⚙️ ЛИЧНЫЙ КАБИНЕТ</b>\n"
        f"{line}\n"
        f"👤 <b>ID аккаунта:</b> <code>{user.telegram_id}</code>\n"
        f"{status_emoji} <b>Статус сети:</b> <i>{status_text}</i>\n"
        f"📅 <b>Доступ до:</b> <code>{sub_end_date}</code>\n"
        f"{line}\n\n"
    )

    if user.is_active:
        sub_url = f"https://{webhook_domain}/webhook/sub/{user.vless_uuid}"

        profile_text += (
            f"🔑 <b>Твой ключ подписки (нажми, чтобы скопировать):</b>\n"
            f"<code>{sub_url}</code>\n\n"
            "<b>📲 Быстрое подключение:</b>\n"
            "• Скопируй ключ выше и вставь его в раздел подписок (Subscription / Sub URL) в приложении.\n"
            "• Telegram не поддерживает прямые ссылки app:// для VPN-приложений, поэтому импорт делается вручную.\n\n"
            "<b>🧭 Инструкция по ручной настройке:</b>\n"
            "1) Открой приложение (Happ, V2rayTun или Hiddify).\n"
            "2) Выбери «Добавить подписку / Import subscription».\n"
            "3) Вставь ключ подписки из этого сообщения.\n"
            "4) Сохрани и обнови подписку (Refresh / Update).\n"
            "5) Выбери любой профиль из списка и подключись.\n\n"
            "<i>💡 Если не подключается: обнови подписку, проверь интернет/дату на устройстве и попробуй другой профиль из списка.</i>"
        )
    else:
        profile_text += "⚠️ <b>Доступ ограничен.</b> Используйте меню ниже, чтобы оплатить и продлить подписку."

    inline_buttons = []
    inline_buttons.extend(
        [
            [InlineKeyboardButton(text="📖 Полная инструкция", url="https://telegra.ph/Instrukciya-po-nastrojke-AnKo-VPN-05-20")],
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="refresh_profile")],
        ]
    )
    
    return profile_text, InlineKeyboardMarkup(inline_keyboard=inline_buttons)


@router.message(F.text.in_({"Профиль", "👤 Профиль"}))
async def profile_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("❌ Профиль не найден. Нажмите /start для регистрации.", reply_markup=main_keyboard)
        return

    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data == "refresh_profile")
async def refresh_profile_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден.", show_alert=True)
        return

    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer("Данные обновлены")
