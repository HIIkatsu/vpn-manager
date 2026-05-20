from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.billing_service import BillingService
from app.services.user_service import UserService

router = Router()


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить и продлить", callback_data="subscription_pay")]]
    )


@router.message(F.text.in_({"Продлить подписку", "💳 Подписка"}))
async def subscription_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.")
        return

    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y %H:%M UTC") if user.sub_end_date else "Не оформлена"

    await message.answer(
        f"<b>Подписка</b>\n"
        f"Статус: {status}\n"
        f"Действует до: <code>{sub_end}</code>\n\n"
        "Продление добавляет 30 дней доступа.",
        reply_markup=subscription_keyboard(),
    )


@router.callback_query(F.data == "subscription_pay")
async def subscription_pay_callback(
    callback: CallbackQuery, user_service: UserService, billing_service: BillingService
) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return

    confirmation_url = await billing_service.create_subscription_payment(user_id=user.id, amount=100.0)
    pay_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Перейти к оплате", url=confirmation_url)]]
    )
    await callback.message.answer("Ссылка на оплату сформирована 👇", reply_markup=pay_keyboard)
    await callback.answer()
