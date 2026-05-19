from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.billing_service import BillingService
from app.services.user_service import UserService

router = Router()


@router.message(F.text == "Продлить подписку")
async def renew_subscription_handler(message: Message, user_service: UserService, billing_service: BillingService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.")
        return

    confirmation_url = await billing_service.create_subscription_payment(user_id=user.id, amount=100.0)
    payment_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=confirmation_url)]])
    await message.answer("Для продления подписки оплатите счет по кнопке ниже:", reply_markup=payment_keyboard)
