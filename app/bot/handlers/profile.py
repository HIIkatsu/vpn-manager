import logging
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.bot.keyboards.main import main_keyboard, main_inline_keyboard
from app.core.settings import settings
from app.services.user_service import UserService
from app.services.traffic_stats_service import TrafficStatsService
from app.services.xray_manager import XrayManager

router = Router()
logger = logging.getLogger(__name__)

LIMIT_BYTES = 1099511627776  # 1 TB

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} TB"

def generate_progress_bar(used_bytes: int, total_bytes: int = LIMIT_BYTES) -> str:
    percent = min(100, int((used_bytes / total_bytes) * 100))
    filled_blocks = int(percent / 10)
    bar = "▓" * filled_blocks + "░" * (10 - filled_blocks)
    return f"[{bar}] {percent}%"

def get_profile_data(user, webhook_domain: str, used_bytes: int = 0):
    if user.sub_end_date:
        sub_end_date = user.sub_end_date.strftime("%d.%m.%Y в %H:%M")
        status_emoji = "🟢" if user.is_active else "🔴"
        status_text = "Активна" if user.is_active else "Истекла"
    else:
        sub_end_date = "Не оформлена"
        status_emoji = "⚪"
        status_text = "Нет подписки"
        
    line = "━━━━━━━━━━━━━━━━━━"
    progress_bar = generate_progress_bar(used_bytes)
    traffic_str = format_bytes(used_bytes)
    
    # Полностью убрали мусор из главного профиля, оставив только стату
    profile_text = (
        f"<b>⚙️ ЛИЧНЫЙ КАБИНЕТ</b>\n"
        f"{line}\n"
        f"🆔 <b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"{status_emoji} <b>Статус:</b> <i>{status_text}</i>\n"
        f"📅 <b>Доступ до:</b> <code>{sub_end_date}</code>\n\n"
        f"📊 <b>Расход трафика (Лимит 1 TB):</b>\n"
        f"<code>{progress_bar}</code>\n"
        f"<i>Использовано: {traffic_str}</i>\n"
        f"{line}\n\n"
    )
    
    inline_buttons = []
    os_name = getattr(user, "preferred_os", "android")
    sub_url = f"https://{webhook_domain}/webhook/sub/{user.vless_uuid}?os={os_name}"
    cabinet_url = f"https://{webhook_domain}/cabinet/{user.vless_uuid}?os={os_name}"
    
    if user.is_active:
        inline_buttons.append([InlineKeyboardButton(text="🌐 Открыть веб-кабинет", url=cabinet_url)])
        inline_buttons.append([InlineKeyboardButton(text="📋 Скопировать ключ", copy_text={"text": sub_url})])
    else:
        profile_text += "⚠️ <b>Доступ ограничен.</b> Перейдите в раздел подписки для оплаты."
        
    inline_buttons.append([InlineKeyboardButton(text="🎁 Бесплатный VPN (Пригласить друга)", callback_data="menu_referral")])
    inline_buttons.extend([
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile"),
            InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_main")
        ]
    ])
    return profile_text, InlineKeyboardMarkup(inline_keyboard=inline_buttons)

async def _fetch_user_traffic_bytes(telegram_id: int, session) -> int:
    try:
        xray = XrayManager()
        stats = await xray.get_live_traffic_stats(reset=False)
        live_bytes = stats.get(str(telegram_id), 0)
        used_bytes = await TrafficStatsService.get_total_with_live(session, telegram_id, live_bytes)
        return used_bytes
    except Exception:
        return 0

@router.message(F.text.in_({"Профиль", "👤 Профиль"}))
async def profile_handler(message: Message, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user: return
    used_bytes = await _fetch_user_traffic_bytes(user.telegram_id, session)
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, used_bytes)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

@router.callback_query(F.data == "menu_profile")
async def inline_profile_handler(callback: CallbackQuery, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    used_bytes = await _fetch_user_traffic_bytes(user.telegram_id, session)
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, used_bytes)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "menu_referral")
async def referral_menu_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return

    bot_username = "AnKoVPN_bot"
    ref_link = f"https://t.me/{bot_username}?start={user.telegram_id}"
    
    # Короткий текст для копирования
    promo_text = (
        "🚀 AnKo VPN — интернет без границ!\n"
        "YouTube 4K и Инста без зависаний.\n\n"
        f"🎁 3 дня бесплатно. Забирай доступ:\n{ref_link}"
    )

    text = (
        "🎁 <b>ПАРТНЕРСКАЯ ПРОГРАММА</b>\n\n"
        "<blockquote>Пользуйтесь VPN <b>абсолютно бесплатно</b>!\n\n"
        "За <b>каждого</b> друга, который перейдет по вашей ссылке и оплатит любую подписку, мы автоматически начислим вам <b>+7 дней</b> премиум-доступа.</blockquote>\n\n"
        "👇 <i>Нажмите кнопку ниже, чтобы скопировать готовое приглашение, и отправьте его друзьям.</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать приглашение", copy_text={"text": promo_text})],
        [InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="menu_profile")]
    ])
    
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer("Реферальная система", show_alert=False)

@router.callback_query(F.data == "refresh_profile")
async def refresh_profile_callback(callback: CallbackQuery, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user:
        used_bytes = await _fetch_user_traffic_bytes(user.telegram_id, session)
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, used_bytes)
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            pass
    await callback.answer("Данные обновлены 🔄")

@router.callback_query(F.data == "open_profile")
async def open_profile_callback(callback: CallbackQuery, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user:
        used_bytes = await _fetch_user_traffic_bytes(user.telegram_id, session)
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, used_bytes)
        await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "force_check_payment")
async def force_check_payment_callback(callback: CallbackQuery):
    from app.core.container import get_billing_service
    from app.db.database import async_session_maker
    
    support_link = "https://t.me/BarsikSneg"
    
    async with async_session_maker() as session:
        billing = get_billing_service(session)
        user = await billing.users.get_by_telegram_id(callback.from_user.id)
        if user is None:
            await callback.answer("Профиль не найден.", show_alert=True)
            return
            
        latest_payment = await billing.payments.get_latest_by_user_id(user.id)
        if not latest_payment:
            await callback.answer("❌ У вас нет созданных платежей.", show_alert=True)
            return
            
        if latest_payment.status in ("pending", "processing"):
            await billing.process_pending()
            await session.refresh(latest_payment)
            
        if latest_payment.status == "success":
            amt = float(latest_payment.amount)
            period_text = "на 1 год" if amt == 900.0 else ("на 3 месяца" if amt == 250.0 else "на 1 месяц")
                
            keyboard_success = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Мой личный кабинет", callback_data="menu_profile")],
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
            ])
            try:
                await callback.message.edit_text(
                    f"✅ <b>Оплата успешно получена!</b>\n\nВы оформили подписку <b>{period_text}</b>. Все настройки конфигурации уже обновлены и ждут вас в личном кабинете.",
                    parse_mode="HTML",
                    reply_markup=keyboard_success
                )
            except Exception:
                pass
            await callback.answer(f"✅ Подписка {period_text} активирована!")
            return
            
        if latest_payment.status in ("pending", "processing"):
            wait_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить статус ещё раз", callback_data="force_check_payment")],
                [InlineKeyboardButton(text="💬 Написать в поддержку", url=support_link)],
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
            ])
            try:
                await callback.message.edit_text(
                    "⏳ <b>Платёж всё ещё в обработке</b>\n\nБанковский шлюз проводит транзакцию. Обычно это занимает от 1 до 3 минут.\n\nЕсли вы уже оплатили, вы можете вернуться в главное меню — подписка включится автоматически.",
                    parse_mode="HTML",
                    reply_markup=wait_keyboard
                )
            except Exception:
                pass
            await callback.answer("⏳ Платёж обрабатывается банком.", show_alert=False)
            return
            
        fail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Связаться с поддержкой", url=support_link)],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
        ])
        try:
            await callback.message.edit_text(
                "❌ <b>Ошибка проведения оплаты</b>\n\nТранзакция была отклонена банком или отменена. Если деньги были списаны — пожалуйста, свяжитесь с поддержкой.",
                parse_mode="HTML",
                reply_markup=fail_keyboard
            )
        except Exception:
            pass
        await callback.answer("❌ Платёж отклонён.", show_alert=True)
