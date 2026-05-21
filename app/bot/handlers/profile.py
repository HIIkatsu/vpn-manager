from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards.main import main_keyboard
from app.core.settings import settings
from app.services.user_service import UserService

router = Router()


def get_profile_data(user, webhook_domain: str):
    if user.sub_end_date:
        sub_end_date = user.sub_end_date.strftime("%d.%m.%Y в %H:%M")
        status_emoji = "🟢" if user.is_active else "🔴"
        status_text = "Активна" if user.is_active else "Истекла"
    else:
        sub_end_date = "Не оформлена"
        status_emoji = "⚪ "
        status_text = "Нет подписки"

    line = "━━━━━━━━━━━━━━━━━━━━━━━━"
    profile_text = (
        f"<b>⚙️ ЛИЧНЫЙ КАБИНЕТ</b>\n"
        f"{line}\n"
        f"👤 <b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"{status_emoji} <b>Статус:</b> <i>{status_text}</i>\n"
        f"📅 <b>Доступ до:</b> <code>{sub_end_date}</code>\n"
        f"{line}\n\n"
    )

    inline_buttons = []
    sub_url = f"https://{webhook_domain}/webhook/sub/{user.vless_uuid}"

    if user.is_active:
        profile_text += (
            "<b>Как подключить:</b>\n"
            "Нажми кнопку ниже, чтобы скопировать ключ, затем вставь его в разделе подписок (Subscription) твоего VPN-клиента и нажми «Обновить»."
        )
        inline_buttons.append([InlineKeyboardButton(text=" Скопировать ключ подписки", copy_text={"text": sub_url})])
    else:
        profile_text += "⚠️ <b>Доступ ограничен.</b> Используйте меню ниже для оплаты."

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
        await message.answer("❌ Профиль не найден.", reply_markup=main_keyboard)
        return
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@router.callback_query(F.data == "refresh_profile")
async def refresh_profile_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer("Данные обновлены")


@router.callback_query(F.data == "open_profile")
async def open_profile_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user:
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "force_check_payment")
async def force_check_payment_callback(callback: CallbackQuery):
    from app.core.container import get_billing_service
    from app.db.database import async_session_maker

    support_link = "https://t.me/NeuroVPN_AI_bot"

    async with async_session_maker() as session:
        billing = get_billing_service(session)
        user = await billing.users.get_by_telegram_id(callback.from_user.id)
        if user is None:
            await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
            return

        await billing.process_pending()
        await session.refresh(user)
        latest_payment = await billing.payments.get_latest_by_user_id(user.id)

    if user.is_active:
        await callback.message.answer("✅ <b>Оплата подтверждена!</b> Подписка активна, можно подключаться.", parse_mode="HTML")
        await callback.answer("Оплата подтверждена ✅", show_alert=True)
        return

    if latest_payment and latest_payment.status == "pending":
        wait_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Связаться с поддержкой", url=support_link)]]
        )
        await callback.message.answer(
            "⏳ Платёж ещё обрабатывается. Обычно это занимает 1–3 минуты.\n"
            "Подождите немного и нажмите проверку ещё раз. Если задержка дольше — напишите в поддержку.",
            reply_markup=wait_keyboard,
        )
        await callback.answer("Платёж еще в обработке", show_alert=True)
        return

    fail_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Связаться с поддержкой", url=support_link)]]
    )
    await callback.message.answer(
        "❌ Не удалось подтвердить оплату автоматически.\n"
        "Пожалуйста, подождите 2–3 минуты и повторите проверку или свяжитесь с поддержкой.",
        reply_markup=fail_keyboard,
    )
    await callback.answer("Оплата пока не найдена", show_alert=True)
