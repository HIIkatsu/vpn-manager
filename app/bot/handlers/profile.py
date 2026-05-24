from urllib.parse import quote
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.bot.keyboards.main import main_keyboard
from app.core.settings import settings
from app.services.user_service import UserService
from app.services.traffic_stats_service import TrafficStatsService
from app.services.xray_manager import XrayManager

router = Router()

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} TB"

def get_profile_data(user, webhook_domain: str, traffic_str: str = "0.00 B"):
    if user.sub_end_date:
        sub_end_date = user.sub_end_date.strftime("%d.%m.%Y в %H:%M")
        status_emoji = "🟢" if user.is_active else "🔴"
        status_text = "Активна" if user.is_active else "Истекла"
    else:
        sub_end_date = "Не оформлена"
        status_emoji = "⚪  "
        status_text = "Нет подписки"
        
    line = "━━━━━━━━━━━━━━━━━━━━━━━━"
    profile_text = (
        f"<b>⚙️ ЛИЧНЫЙ КАБИНЕТ</b>\n"
        f"{line}\n"
        f"👤 <b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"{status_emoji} <b>Статус:</b> <i>{status_text}</i>\n"
        f"📅 <b>Доступ до:</b> <code>{sub_end_date}</code>\n"
        f"📊 <b>Расход трафика:</b> <code>{traffic_str}</code>\n"
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
            [InlineKeyboardButton(text="📖 Полная инструкция", url="https://neurosmmai.ru/setup")],
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="refresh_profile")],
        ]
    )
    return profile_text, InlineKeyboardMarkup(inline_keyboard=inline_buttons)

async def _fetch_user_traffic(telegram_id: int, session) -> str:
    try:
        xray = XrayManager()
        stats = await xray.get_live_traffic_stats(reset=True)
        live_bytes = stats.get(str(telegram_id), 0)
        used_bytes = await TrafficStatsService.persist_and_get_total(session, telegram_id, live_bytes)
        return format_bytes(used_bytes)
    except Exception:
        return "0.00 B"

@router.message(F.text.in_({"Профиль", "👤 Профиль"}))
async def profile_handler(message: Message, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("❌ Профиль не найден.", reply_markup=main_keyboard)
        return
    
    traffic = await _fetch_user_traffic(user.telegram_id, session)
    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, traffic)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

@router.callback_query(F.data == "refresh_profile")
async def refresh_profile_callback(callback: CallbackQuery, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user:
        traffic = await _fetch_user_traffic(user.telegram_id, session)
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, traffic)
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            pass
    await callback.answer("Данные обновлены")

@router.callback_query(F.data == "open_profile")
async def open_profile_callback(callback: CallbackQuery, user_service: UserService, session) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user:
        traffic = await _fetch_user_traffic(user.telegram_id, session)
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, traffic)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
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
            await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
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
            if amt == 900.0:
                period_text = "на 1 год"
            elif amt == 250.0:
                period_text = "на 3 месяца"
            else:
                period_text = "на 1 месяц"
                
            keyboard_success = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="open_profile")]
            ])
            try:
                await callback.message.edit_text(
                    f"✅ <b>Оплата успешно получена!</b>\nВы оформили/продлили подписку <b>{period_text}</b>. Все настройки и ключи уже ждут вас в личном кабинете.",
                    parse_mode="HTML",
                    reply_markup=keyboard_success
                )
            except Exception:
                pass
            await callback.answer(f"✅ Подписка {period_text} activated!")
            return
            
        if latest_payment.status in ("pending", "processing"):
            wait_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить статус ещё раз", callback_data="force_check_payment")],
                [InlineKeyboardButton(text="💬 Написать в поддержку", url=support_link)]
            ])
            try:
                await callback.message.edit_text(
                    "⏳ <b>Платёж в обработке</b>\n\nБанк всё ещё проводит транзакцию. Обычно это занимает 1–3 минуты.\nЕсли деньги списались, но подписки нет дольше 5 минут — смело пишите в поддержку.",
                    parse_mode="HTML",
                    reply_markup=wait_keyboard
                )
            except Exception:
                pass
                
            await callback.answer("⏳ Платёж всё ещё обрабатывается банком.", show_alert=True)
            return
            
        fail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Связаться с поддержкой", url=support_link)]
        ])
        try:
            await callback.message.edit_text(
                "❌ <b>Ошибка оплаты</b>\nТранзакция отменена шлюзом или отклонена банком.\nЕсли деньги списались — напишите в поддержку.",
                parse_mode="HTML",
                reply_markup=fail_keyboard
            )
        except Exception:
            pass
        await callback.answer("❌ Платёж отклонён.", show_alert=True)
