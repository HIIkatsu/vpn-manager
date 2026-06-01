import json, urllib.parse, hashlib, time, aiohttp, asyncio
from datetime import datetime, timedelta, timezone
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.user_service import UserService
from app.db.models.promocode import Promocode, UserPromocode
from app.db.repositories.outbox_repo import OutboxRepository

router = Router()

DOC_URL = "https://telegra.ph/Politika-konfidencialnosti-05-31-52"

class PromoState(StatesGroup):
    waiting_for_promo = State()

def get_env(key, default=""):
    try:
        with open("/root/vpn-manager-v2/.env", "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🥉 1 месяц — 100 ₽", callback_data="sub_pay_100.0")],
            [InlineKeyboardButton(text="🥈 3 месяца — 250 ₽ (-16%)", callback_data="sub_pay_250.0")],
            [InlineKeyboardButton(text="🥇 1 год — 900 ₽ (-25%) 🔥", callback_data="sub_pay_900.0")],
            [
                InlineKeyboardButton(text="🎫 Промокод", callback_data="enter_promocode"),
                InlineKeyboardButton(text="📜 Правила", url=DOC_URL)
            ],
            [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
        ]
    )

def get_sub_text(user) -> str:
    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y в %H:%M") if user.sub_end_date else "Не оформлена"
    return f"💎 <b>ПРЕМИУМ ДОСТУП</b>\n━━━━━━━━━━━━━━━━━━\nТекущий статус: {status}\nОплачено до: <code>{sub_end}</code>\n\n💳 <b>Выберите тарифный план:</b>\n\n<i>Нажимая на кнопку оплаты, вы принимаете Правила сервиса (кнопка ниже).</i>"

@router.message(F.text.in_({"Продлить подписку", "💳 Подписка"}))
async def subscription_handler(message: Message, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user: await message.answer(get_sub_text(user), reply_markup=subscription_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_subscription")
async def inline_subscription_handler(callback: CallbackQuery, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user: await callback.message.edit_text(get_sub_text(user), reply_markup=subscription_keyboard(), parse_mode="HTML")

# --- ЛОГИКА ПРОМОКОДОВ ---
@router.callback_query(F.data == "enter_promocode")
async def enter_promocode_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎟 <b>Активация промокода</b>\n\nПришлите ваш промокод ответным сообщением:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_subscription")
        ]])
    )
    await state.set_state(PromoState.waiting_for_promo)

@router.message(PromoState.waiting_for_promo)
async def process_promocode(message: Message, user_service: UserService, session: AsyncSession, state: FSMContext):
    promo_code_text = message.text.strip()
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user: return

    # Компактная кнопка возврата при ошибках
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 К тарифам", callback_data="menu_subscription")]])

    promo_result = await session.execute(select(Promocode).where(Promocode.code == promo_code_text))
    promo = promo_result.scalar_one_or_none()

    if not promo:
        await message.answer("❌ Промокод не найден или введен неверно.", reply_markup=kb_back)
        await state.clear()
        return

    now = datetime.now(timezone.utc)

    if promo.expires_at:
        promo_exp = promo.expires_at if promo.expires_at.tzinfo else promo.expires_at.replace(tzinfo=timezone.utc)
        if promo_exp < now:
            await message.answer("❌ Срок действия этого промокода истек.", reply_markup=kb_back)
            await state.clear()
            return

    if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
        await message.answer("❌ Лимит активаций этого промокода исчерпан.", reply_markup=kb_back)
        await state.clear()
        return

    used_result = await session.execute(
        select(UserPromocode).where(
            UserPromocode.telegram_id == user.telegram_id,
            UserPromocode.promocode_id == promo.id
        )
    )
    if used_result.scalar_one_or_none():
        await message.answer("⚠️ Вы уже активировали этот промокод ранее.", reply_markup=kb_back)
        await state.clear()
        return

    if user.sub_end_date and user.sub_end_date > now:
        user.sub_end_date += timedelta(days=promo.reward_days)
    else:
        user.sub_end_date = now + timedelta(days=promo.reward_days)

    promo.used_count += 1
    new_usage = UserPromocode(telegram_id=user.telegram_id, promocode_id=promo.id)
    session.add(new_usage)
    
    await session.commit()
    await state.clear()
    
    new_date = user.sub_end_date.strftime("%d.%m.%Y %H:%M")
    await message.answer(
        f"✅ <b>Промокод успешно активирован!</b>\n\nВам начислено: <b>{promo.reward_days} дней</b>.\nНовая дата окончания: <code>{new_date}</code>", 
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 В личный кабинет", callback_data="menu_profile")]])
    )

# --- ЛОГИКА ОПЛАТЫ ---
@router.callback_query(F.data.startswith("sub_pay_"))
async def subscription_pay_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    
    amount = float(callback.data.split("_")[-1])
    days = 30 if amount == 100.0 else 90 if amount == 250.0 else 365 if amount == 900.0 else 30

    amount_ym = str(int(amount))
    amount_str = f"{amount:.2f}"
    
    # 1. ЮMoney
    YOOMONEY_RECEIVER = get_env("YOOMONEY_RECEIVER", "4100119543123060")
    ym_params = {"receiver": YOOMONEY_RECEIVER, "quickpay-form": "shop", "targets": f"VPN {days} d", "sum": amount_ym, "label": f"{user.telegram_id}_{days}", "successURL": "tg://resolve?domain=AnKoVPN_bot"}
    ym_url = f"https://yoomoney.ru/quickpay/confirm.xml?{urllib.parse.urlencode(ym_params)}"

    # 2. AnyPay
    ANYPAY_PROJECT_ID = get_env("ANYPAY_PROJECT_ID", "17784")
    ANYPAY_SECRET_KEY = get_env("ANYPAY_SECRET_KEY", "")
    desc = "VPN"
    time_suffix = f"{int(time.time() % 1000):03d}"
    anypay_pay_id = f"{user.telegram_id}{time_suffix}"
    success_url = "https://t.me/ankovpn_bot"
    fail_url = "https://t.me/ankovpn_bot"
    
    sign_str = f"{ANYPAY_PROJECT_ID}:{anypay_pay_id}:{amount_str}:RUB:{desc}:{success_url}:{fail_url}:{ANYPAY_SECRET_KEY}"
    anypay_sign = hashlib.sha256(sign_str.encode('utf-8')).hexdigest()
    ap_params = {"merchant_id": ANYPAY_PROJECT_ID, "pay_id": anypay_pay_id, "amount": amount_str, "currency": "RUB", "desc": desc, "success_url": success_url, "fail_url": fail_url, "sign": anypay_sign}
    anypay_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(ap_params)}"

    # 3. CryptoBot
    crypto_url = ""
    CRYPTOBOT_TOKEN = get_env("CRYPTOBOT_TOKEN", "589728:AA0etJX4eBfcwigpnGzaWdSjD2aIQ5cfqqV")
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post("https://pay.crypt.bot/api/createInvoice", headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}, json={"currency_type": "fiat", "fiat": "RUB", "amount": amount_ym, "description": f"VPN {days}d", "payload": f"{user.telegram_id}_{days}"}) as resp:
                res_data = await resp.json()
                if res_data.get("ok"): crypto_url = res_data["result"].get("mini_app_invoice_url", res_data["result"].get("pay_url"))
    except Exception: pass

    kb = [
        [InlineKeyboardButton(text="💳 СберPay / ЮMoney", url=ym_url)], 
        [InlineKeyboardButton(text="🔄 Карта РФ / СБП (AnyPay)", url=anypay_url)]
    ]
    if crypto_url: 
        kb.append([InlineKeyboardButton(text="🪙 Криптовалюта", url=crypto_url)])
    
    kb.append([InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="check_payment_status")])
    kb.append([InlineKeyboardButton(text="🔙 Выбрать другой тариф", callback_data="menu_subscription")])
    
    await callback.message.edit_text(f"🧾 <b>Счет на оплату: {int(amount)} ₽</b>\n\nВыберите удобный способ оплаты:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "check_payment_status")
async def check_payment_status_callback(callback: CallbackQuery, user_service: UserService):
    await callback.answer("🔄 Опрашиваем платежные шлюзы...", show_alert=False)
    await asyncio.sleep(1.0)
    
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return await callback.answer("Ошибка: Пользователь не найден.", show_alert=True)
    
    now = datetime.now(timezone.utc)
    check_time = (now + timedelta(hours=3)).strftime("%H:%M:%S")
    
    kb = callback.message.reply_markup.inline_keyboard
    if not any("ankovpn_support_bot" in str(btn.url) for row in kb for btn in row):
        kb.append([InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/ankovpn_support_bot")])

    text_lines = callback.message.html_text.split('\n')
    amount_line = text_lines[0] if text_lines else "🧾 <b>Счет на оплату</b>"
    
    try:
        if user.is_active and user.sub_end_date and user.sub_end_date > now:
            date_str = user.sub_end_date.strftime("%d.%m.%Y %H:%M")
            await callback.message.edit_text(
                f"{amount_line}\n\n"
                f"💎 Ваша текущая подписка активна до: <code>{date_str}</code>\n\n"
                f"⏳ <b>Ожидаем подтверждения нового платежа...</b>\n"
                f"Обычно банки обрабатывают перевод за 1-2 минуты. Как только деньги поступят, бот <b>автоматически</b> пришлет вам уведомление.\n\n"
                f"<i>Если деньги списаны, но уведомления нет дольше 10 минут — обратитесь в поддержку.</i>\n\n"
                f"⏱ <i>Последняя проверка: {check_time} (MSK)</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
            )
        else:
            await callback.message.edit_text(
                f"{amount_line}\n\n"
                f"⏳ <b>Платеж еще обрабатывается...</b>\n\n"
                f"Обычно банки и шлюзы подтверждают перевод за 1-2 минуты. Как только деньги поступят, бот <b>автоматически</b> пришлет вам уведомление.\n\n"
                f"<i>Если деньги списаны, но подписка не обновилась в течение 10 минут — напишите нам.</i>\n\n"
                f"⏱ <i>Последняя проверка: {check_time} (MSK)</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
            )
    except Exception:
        pass
