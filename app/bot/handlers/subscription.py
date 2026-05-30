import json
from datetime import datetime, timedelta, timezone
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.billing_service import BillingService
from app.services.user_service import UserService
from app.db.models.promocode import Promocode, UserPromocode
from app.db.repositories.outbox_repo import OutboxRepository

router = Router()

class PromoState(StatesGroup):
    waiting_for_promo = State()

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🥉 1 месяц — 100 ₽", callback_data="sub_pay_100.0")],
            [InlineKeyboardButton(text="🥈 3 месяца — 250 ₽ (Выгода 16%)", callback_data="sub_pay_250.0")],
            [InlineKeyboardButton(text="🥇 1 год — 900 ₽ (Выгода 25%) 🔥", callback_data="sub_pay_900.0")],
            [InlineKeyboardButton(text="🎫 Ввести промокод", callback_data="enter_promocode")],
            [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
        ]
    )

def get_sub_text(user) -> str:
    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y в %H:%M") if user.sub_end_date else "Не оформлена"
    return (
        f"💎 <b>ПРЕМИУМ ДОСТУП</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Текущий статус: {status}\n"
        f"Оплачено до: <code>{sub_end}</code>\n\n"
        f"🚀 <b>Что дает подписка?</b>\n"
        f"• Максимальная скорость для <b>4K-видео</b> и тяжелых файлов.\n"
        f"• Одновременная работа на <b>5 устройствах</b>.\n"
        f"• Доступ ко всей сети наших серверов (Россия + Европа).\n"
        f"• Умный обход блокировок без отключения VPN для банков.\n"
        f"• Поддержка 24/7 и помощь в настройке.\n\n"
        f"💳 <b>Выберите тарифный план:</b>\n"
        f"<i>При продлении оставшиеся дни плюсуются к новому сроку.</i>"
    )

@router.message(F.text.in_({"Продлить подписку", "💳 Подписка"}))
async def subscription_handler(message: Message, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user: return
    await message.answer(get_sub_text(user), reply_markup=subscription_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_subscription")
async def inline_subscription_handler(callback: CallbackQuery, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    await callback.message.edit_text(get_sub_text(user), reply_markup=subscription_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "enter_promocode")
async def enter_promo_callback(callback: CallbackQuery, state: FSMContext) -> None:
    text = (
        "🎫 <b>АКТИВАЦИЯ ПРОМОКОДА</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте ваш промокод ответным сообщением в этот чат."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_subscription")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(PromoState.waiting_for_promo)
    await callback.answer()

# Обрабатываем текст промокода (исключая нажатия на системные кнопки меню)
@router.message(PromoState.waiting_for_promo, ~F.text.in_({"🚀 Подключить VPN", "👤 Профиль", "💳 Подписка", "📖 Инструкция", "💬 Поддержка"}))
async def process_promo_message(message: Message, state: FSMContext, session: AsyncSession, user_service: UserService) -> None:
    await state.clear()
    code = message.text.strip().upper()
    
    result = await session.execute(select(Promocode).where(Promocode.code == code))
    promo = result.scalars().first()

    if not promo:
        await message.answer("❌ <b>Промокод не найден или введен неверно.</b>", parse_mode="HTML")
        return

    now = datetime.now(timezone.utc)
    if promo.expires_at and promo.expires_at < now:
        await message.answer("❌ <b>Срок действия промокода истёк.</b>", parse_mode="HTML")
        return

    if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
        await message.answer("❌ <b>Лимит активаций этого промокода исчерпан.</b>", parse_mode="HTML")
        return

    used_check = await session.execute(
        select(UserPromocode).where(
            UserPromocode.telegram_id == message.from_user.id,
            UserPromocode.promocode_id == promo.id
        )
    )
    if used_check.scalars().first():
        await message.answer("❌ <b>Вы уже активировали этот промокод ранее.</b>", parse_mode="HTML")
        return

    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user:
        return

    # Начисляем дни
    if user.sub_end_date is None or user.sub_end_date < now:
        user.sub_end_date = now + timedelta(days=promo.reward_days)
    else:
        user.sub_end_date += timedelta(days=promo.reward_days)
    
    user.is_active = True
    promo.used_count += 1
    
    # Записываем, что этот юзер использовал этот промокод
    session.add(UserPromocode(telegram_id=message.from_user.id, promocode_id=promo.id))
    await session.commit()

    # Отправляем задачу в Xray на немедленное включение интернета
    outbox = OutboxRepository(session)
    await outbox.enqueue(
        event_type="xray.add_client",
        aggregate_type="promo_reward",
        aggregate_id=str(user.id),
        dedup_key=f"xray.add_client:promo_{user.id}_{int(now.timestamp())}",
        payload_json=json.dumps({"telegram_id": user.telegram_id, "uuid": user.vless_uuid}),
    )
    await session.commit()

    success_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Перейти в Личный кабинет", callback_data="menu_profile")]
    ])
    await message.answer(f"✅ <b>Промокод «{code}» успешно активирован!</b>\n\nВам начислено <b>+{promo.reward_days} дней</b> премиум-доступа.", parse_mode="HTML", reply_markup=success_kb)

@router.callback_query(F.data.startswith("sub_pay_"))
async def subscription_pay_callback(
    callback: CallbackQuery, user_service: UserService, billing_service: BillingService
) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
        
    amount = float(callback.data.split("_")[-1])
    confirmation_url = await billing_service.create_subscription_payment(user_id=user.id, amount=amount)
    
    pay_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить картой / СБП", url=confirmation_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="force_check_payment")],
            [InlineKeyboardButton(text="🔙 Выбрать другой тариф", callback_data="menu_subscription")]
        ]
    )
    
    text = (
        f"🧾 <b>Счет на оплату: {int(amount)} ₽</b>\n\n"
        "1. Нажмите кнопку «Оплатить картой / СБП».\n"
        "2. После успешной транзакции бот <b>автоматически</b> продлит подписку.\n\n"
        "<i>⏳ Если деньги списались, но статус не обновился — нажмите «Проверить оплату».</i>"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=pay_keyboard)
    await callback.answer()
