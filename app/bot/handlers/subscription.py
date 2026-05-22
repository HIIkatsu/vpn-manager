from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.services.billing_service import BillingService
from app.services.user_service import UserService

router = Router()

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 1 месяц — 100₽", callback_data="sub_pay_100.0")],
            [InlineKeyboardButton(text="👤 3 месяца — 250₽ (-16%) 🔥", callback_data="sub_pay_250.0")],
            [InlineKeyboardButton(text="👤 1 год — 900₽ (-25%) 🚀", callback_data="sub_pay_900.0")]
        ]
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
        "Выберите тарифный план для продления доступа:",
        reply_markup=subscription_keyboard(),
    )

@router.callback_query(F.data.startswith("sub_pay_"))
async def subscription_pay_callback(
    callback: CallbackQuery, user_service: UserService, billing_service: BillingService
) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return
        
    amount = float(callback.data.split("_")[-1])
    confirmation_url = await billing_service.create_subscription_payment(user_id=user.id, amount=amount)
    
    pay_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↗️ Перейти к оплате", url=confirmation_url)],
            [InlineKeyboardButton(text="🔄 Я оплатил, проверить статус", callback_data="force_check_payment")]
        ]
    )
    
    text = (
        f"💳 <b>Ссылка на оплату сформирована ({int(amount)}₽)</b>\n\n"
        "Обычно платеж проходит моментально, но иногда банковские шлюзы обрабатывают его 1-3 минуты.\n"
        "Как только деньги поступят, бот <b>автоматически</b> пришлет уведомление и включит VPN.\n\n"
        "<i>Если вы уже оплатили, но ничего не происходит — нажмите кнопку ниже.</i>"
    )
    
    await callback.message.answer(text, parse_mode="HTML", reply_markup=pay_keyboard)
    await callback.answer()
