import urllib.parse, hashlib, time, aiohttp, asyncio
from datetime import datetime, timedelta, timezone
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.user_service import UserService
from app.db.models.promocode import Promocode, UserPromocode
from app.db.models import Payment
from app.core.container import get_billing_service

router = Router()
DOC_URL = "https://telegra.ph/Politika-konfidencialnosti-05-31-52"
class PromoState(StatesGroup): waiting_for_promo = State()

def get_env(key, default=""):
    try:
        with open("/root/vpn-manager-v2/.env", "r") as f:
            for line in f:
                if line.startswith(f"{key}="): return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception: pass
    return default

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥉 1 месяц — 100 ₽", callback_data="sub_pay_100.0")],
        [InlineKeyboardButton(text="🥈 3 месяца — 250 ₽ (-16%)", callback_data="sub_pay_250.0")],
        [InlineKeyboardButton(text="🥇 1 год — 900 ₽ (-25%) 🔥", callback_data="sub_pay_900.0")],
        [InlineKeyboardButton(text="🎫 Промокод", callback_data="enter_promocode"), InlineKeyboardButton(text="📜 Правила", url=DOC_URL)],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])

@router.message(F.text.in_({"Продлить подписку", "💳 Подписка"}))
async def subscription_handler(message: Message, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user: return
    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y в %H:%M") if user.sub_end_date else "Не оформлена"
    await message.answer(f"💎 <b>ПРЕМИУМ ДОСТУП</b>\n━━━━━━━━━━━━━━━━━━\nТекущий статус: {status}\nОплачено до: <code>{sub_end}</code>\n\n💳 <b>Выберите тарифный план:</b>\n\n<i>Нажимая на кнопку оплаты, вы принимаете Правила сервиса (кнопка ниже).</i>", reply_markup=subscription_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_subscription")
async def inline_subscription_handler(callback: CallbackQuery, user_service: UserService, state: FSMContext) -> None:
    await state.clear()
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    status = "🟢 Активна" if user.is_active else "🔴 Неактивна"
    sub_end = user.sub_end_date.strftime("%d.%m.%Y в %H:%M") if user.sub_end_date else "Не оформлена"
    await callback.message.edit_text(f"💎 <b>ПРЕМИУМ ДОСТУП</b>\n━━━━━━━━━━━━━━━━━━\nТекущий статус: {status}\nОплачено до: <code>{sub_end}</code>\n\n💳 <b>Выберите тарифный план:</b>\n\n<i>Нажимая на кнопку оплаты, вы принимаете Правила сервиса (кнопка ниже).</i>", reply_markup=subscription_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "enter_promocode")
async def enter_promocode_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎟 <b>Активация промокода</b>\n\nПришлите ваш промокод ответным сообщением:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_subscription")]]))
    await state.set_state(PromoState.waiting_for_promo)

@router.message(PromoState.waiting_for_promo)
async def process_promocode(message: Message, user_service: UserService, session: AsyncSession, state: FSMContext):
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user: return
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 К тарифам", callback_data="menu_subscription")]])
    promo = (await session.execute(select(Promocode).where(Promocode.code == message.text.strip()))).scalar_one_or_none()
    if not promo: return await message.answer("❌ Промокод не найден.", reply_markup=kb_back)
    now = datetime.now(timezone.utc)
    if promo.expires_at and (promo.expires_at if promo.expires_at.tzinfo else promo.expires_at.replace(tzinfo=timezone.utc)) < now: return await message.answer("❌ Срок действия истек.", reply_markup=kb_back)
    if promo.max_uses > 0 and promo.used_count >= promo.max_uses: return await message.answer("❌ Лимит исчерпан.", reply_markup=kb_back)
    if (await session.execute(select(UserPromocode).where(UserPromocode.telegram_id == user.telegram_id, UserPromocode.promocode_id == promo.id))).scalar_one_or_none(): return await message.answer("⚠️ Вы уже активировали этот код.", reply_markup=kb_back)
    
    if user.sub_end_date and user.sub_end_date > now: user.sub_end_date += timedelta(days=promo.reward_days)
    else: user.sub_end_date = now + timedelta(days=promo.reward_days)
    promo.used_count += 1
    session.add(UserPromocode(telegram_id=user.telegram_id, promocode_id=promo.id))
    await session.commit()
    await state.clear()
    await message.answer(f"✅ <b>Промокод активирован!</b>\n\nНачислено: <b>{promo.reward_days} дней</b>.\nДо: <code>{user.sub_end_date.strftime('%d.%m.%Y %H:%M')}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 В кабинет", callback_data="menu_profile")]]))

@router.callback_query(F.data.startswith("sub_pay_"))
async def subscription_pay_callback(callback: CallbackQuery, user_service: UserService, session: AsyncSession) -> None:
    await callback.message.edit_text("⏳ Формируем счет...", parse_mode="HTML")
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    amount = float(callback.data.split("_")[-1])
    days, amount_ym, amount_str = (30 if amount == 100.0 else 90 if amount == 250.0 else 365), str(int(amount)), f"{amount:.2f}"
    
    yk_url = ""
    try:
        from app.services.yookassa_service import YooKassaService
        from app.db.repositories.payment_repo import PaymentRepository
        yk_service = YooKassaService()
        yk_url = await yk_service.create_payment(PaymentRepository(session), user.id, amount, "https://t.me/ankovpn_bot")
        await session.commit()
    except Exception: pass

    ANYPAY_PROJECT_ID, ANYPAY_SECRET_KEY = get_env("ANYPAY_PROJECT_ID", "17784"), get_env("ANYPAY_SECRET_KEY", "")
    anypay_pay_id = f"{user.telegram_id}{int(time.time() % 1000):03d}"
    ap_params = {"merchant_id": ANYPAY_PROJECT_ID, "pay_id": anypay_pay_id, "amount": amount_str, "currency": "RUB", "desc": "VPN", "success_url": "https://t.me/ankovpn_bot", "fail_url": "https://t.me/ankovpn_bot", "sign": hashlib.sha256(f"{ANYPAY_PROJECT_ID}:{anypay_pay_id}:{amount_str}:RUB:VPN:https://t.me/ankovpn_bot:https://t.me/ankovpn_bot:{ANYPAY_SECRET_KEY}".encode()).hexdigest()}
    anypay_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(ap_params)}"

    crypto_url, CRYPTOBOT_TOKEN = "", get_env("CRYPTOBOT_TOKEN", "")
    if CRYPTOBOT_TOKEN:
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post("https://pay.crypt.bot/api/createInvoice", headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}, json={"currency_type": "fiat", "fiat": "RUB", "amount": amount_ym, "description": f"VPN {days}d", "payload": f"{user.telegram_id}_{days}"}) as resp:
                    if resp.status == 200:
                        res_data = await resp.json()
                        if res_data.get("ok"): crypto_url = res_data["result"].get("pay_url", "").replace("https://t.me/", "tg://resolve?domain=").replace("?start=", "&start=")
        except Exception: pass

    kb = []
    if yk_url: kb.append([InlineKeyboardButton(text="💳 Карта РФ / СБП (ЮKassa)", url=yk_url)])
    kb.append([InlineKeyboardButton(text="🔄 Запасной шлюз (AnyPay)", url=anypay_url)])
    if crypto_url: kb.append([InlineKeyboardButton(text="🪙 Криптовалюта", url=crypto_url)])
    kb.append([InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="check_payment_status")])
    kb.append([InlineKeyboardButton(text="🔙 Выбрать другой тариф", callback_data="menu_subscription")])
    await callback.message.edit_text(f"🧾 <b>Счет на оплату: {int(amount)} ₽</b>\n\nВыберите удобный способ оплаты:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "check_payment_status")
async def check_payment_status_callback(callback: CallbackQuery, user_service: UserService, session: AsyncSession):
    await callback.answer("🔄 Запрашиваем статус в ЮКассе...", show_alert=False)
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    
    payment_found = False
    try:
        from app.services.yookassa_service import YooKassaService
        billing = get_billing_service(session)
        pending_payments = (await session.execute(select(Payment).where(Payment.user_id == user.id, Payment.status == "pending"))).scalars().all()
        if pending_payments:
            yk = YooKassaService()
            for p in pending_payments:
                try:
                    remote = await yk.fetch_remote_payment(p.payment_id)
                    if remote and remote.status == "succeeded":
                        res = await billing.activate_payment(p.payment_id, f"manual_check_{p.payment_id}")
                        await session.commit()
                        if res: payment_found = True
                        break
                except Exception: pass
    except Exception as e: print("Check error:", e)

    user = await user_service.get_by_telegram_id(callback.from_user.id)
    now = datetime.now(timezone.utc)
    check_time = (now + timedelta(hours=3)).strftime("%H:%M:%S")
    
    if payment_found or (user.is_active and user.sub_end_date and user.sub_end_date > now):
        # 1. Сворачиваем старое сообщение в чек
        try: await callback.message.edit_text(f"🧾 <b>Счет оплачен</b>\n\n💎 Ваша подписка активна до: <code>{user.sub_end_date.strftime('%d.%m.%Y %H:%M')}</code>", parse_mode="HTML")
        except Exception: pass
        # 2. Шлем ОТДЕЛЬНЫЙ пуш
        if payment_found:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 В личный кабинет", callback_data="menu_profile")]])
            await callback.bot.send_message(chat_id=callback.from_user.id, text="✅ <b>Оплата успешно получена!</b>\n\nПодписка продлена, приятного пользования!", parse_mode="HTML", reply_markup=kb)
    else:
        kb = callback.message.reply_markup.inline_keyboard
        if not any("ankovpn_support_bot" in str(btn.url) for row in kb for btn in row): kb.append([InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/ankovpn_support_bot")])
        text_lines = callback.message.html_text.split('\n')
        amount_line = text_lines[0] if text_lines else "🧾 <b>Счет на оплату</b>"
        await callback.message.edit_text(f"{amount_line}\n\n⏳ <b>Платеж еще обрабатывается...</b>\n\nОбычно банки подтверждают перевод за 1-2 минуты. Как только деньги поступят, бот <b>автоматически</b> пришлет вам уведомление.\n\n⏱ <i>Последняя проверка: {check_time} (MSK)</i>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
