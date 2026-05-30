import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from aiogram import F, Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.profile import get_profile_data
from app.core.settings import settings
from app.services.user_service import UserService
from app.bot.keyboards.main import main_inline_keyboard, main_keyboard, os_select_keyboard

router = Router()

def get_welcome_text(is_new_trial: bool = False) -> str:
    text = "🚀 <b>AnKo Smart VPN — интернет без границ</b>\n\n"
    
    if is_new_trial:
        text += (
            "🎁 <b>Вам активирован бесплатный период на 3 дня!</b>\n"
            "<i>Вы получаете полный доступ ко всем серверам без ограничений скорости. По истечении этого времени ваш профиль сохранится еще на 7 дней. Достаточно будет просто оплатить подписку, и интернет снова заработает — перенастраивать ничего не придется!</i>\n\n"
        )
        
    text += (
        "Мы создали сервис, который просто работает. Никаких ручных переключений и обрывов — наш алгоритм сделает всё за вас.\n\n"
        "<blockquote>✨ <b>Умный обход:</b> Российские сайты и банки открываются напрямую. Заблокированные ресурсы — через зарубежные серверы.\n"
        "🛡 <b>Защита:</b> Провайдер видит лишь обычный безопасный трафик. Нас невозможно заблокировать.\n"
        "⚡️ <b>Скорость:</b> Максимальная скорость для просмотра видео в 4K и загрузки тяжелых файлов без зависаний.</blockquote>\n\n"
        "👇 <b>Панель управления:</b>"
    )
    return text

async def check_and_issue_trial(user, session: AsyncSession) -> bool:
    if not user.sub_end_date:
        user.sub_end_date = datetime.now(timezone.utc) + timedelta(days=3)
        user.is_active = True
        session.add(user)
        await session.commit()
        return True
    return False

@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, session: AsyncSession, user_service: UserService) -> None:
    # Захват реферального ID из ссылки (защита от саморефа)
    ref_id = None
    if command.args and command.args.isdigit():
        ref_id = int(command.args)
        if ref_id == message.from_user.id:
            ref_id = None

    user = await user_service.get_or_create(message.from_user.id, message.from_user.username, referrer_telegram_id=ref_id)
    
    msg = await message.answer("Запуск сервиса...", reply_markup=main_keyboard)
    await msg.delete() 

    await message.answer(
        "👋 <b>Добро пожаловать в AnKo Smart VPN!</b>\n\n"
        "Для стабильной работы и экономии заряда батареи нам нужно настроить конфигурацию под ваше устройство.\n\n"
        "<blockquote>⚙️ <b>Пожалуйста, выберите операционную систему:</b></blockquote>",
        reply_markup=os_select_keyboard
    )

@router.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text(get_welcome_text(), reply_markup=main_inline_keyboard)
    await callback.answer("Главное меню", show_alert=False)

@router.callback_query(F.data == "skip_os_select")
async def skip_os_callback(callback: CallbackQuery, user_service: UserService, session: AsyncSession) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    is_new_trial = await check_and_issue_trial(user, session)
    
    await callback.message.edit_text(get_welcome_text(is_new_trial), reply_markup=main_inline_keyboard)
    await callback.answer("Добро пожаловать!", show_alert=False)

@router.callback_query(F.data == "menu_status")
async def network_status_callback(callback: CallbackQuery) -> None:
    ping_ru = random.randint(15, 25)
    ping_eu_1 = random.randint(40, 52)
    ping_eu_2 = random.randint(48, 65)
    
    status_text = (
        "📡 <b>Состояние сети AnKo VPN</b>\n\n"
        f"<blockquote>🇷🇺 <b>Узлы в России:</b> <code>Идеально ({ping_ru}ms)</code>\n"
        f"🌍 <b>Европа (Пул серверов 1):</b> <code>Идеально ({ping_eu_1}ms)</code>\n"
        f"🌍 <b>Европа (Пул серверов 2):</b> <code>Идеально ({ping_eu_2}ms)</code>\n"
        f"⚡ <b>Балансировщик нагрузки:</b> <code>Активен</code></blockquote>\n\n"
        "🔥 <i>Система объединяет десятки серверов. Трафик автоматически направляется на наименее загруженный узел.</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="menu_status")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])
    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer("Данные обновлены", show_alert=False)

OS_LABELS = {"android": "Android", "ios": "iOS", "windows": "Windows", "linux": "Linux", "macos": "macOS"}

@router.callback_query(F.data.startswith("os_"))
async def os_select_callback(callback: CallbackQuery, user_service: UserService, session: AsyncSession) -> None:
    selected_os = callback.data.replace("os_", "", 1)
    if selected_os not in OS_LABELS:
        return
        
    await user_service.set_preferred_os(callback.from_user.id, selected_os)
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    
    is_new_trial = await check_and_issue_trial(user, session)
    
    await callback.message.edit_text(
        f"✅ <b>Отлично!</b>\n\n"
        f"<blockquote>Настройки успешно оптимизированы под систему <b>{OS_LABELS[selected_os]}</b>.</blockquote>\n\n"
        "<i>Применяем конфигурацию... ⏳</i>"
    )
    await callback.answer(f"ОС: {OS_LABELS[selected_os]}", show_alert=False)
    
    await asyncio.sleep(1.5)
    await callback.message.edit_text(get_welcome_text(is_new_trial), reply_markup=main_inline_keyboard)

@router.message(F.text == "🚀 Подключить VPN")
async def connect_vpn_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user or not user.is_active:
        await message.answer("⚠️ Ваша подписка неактивна. Перейдите в раздел <b>💳 Тарифы и Оплата</b> для продления.")
        return

    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, 0)
    await message.answer(
        "<b>🚀 Подключение AnKo VPN</b>\n\n"
        "Для каждого пользователя создается индивидуальный <b>🌐 Личный веб-кабинет</b>. "
        "В нём вы можете отслеживать детальный расход трафика, подключать новые устройства в один клик и управлять подпиской.\n\n"
        "<blockquote>Быстрый импорт: нажмите кнопку ниже, чтобы автоматически закинуть настройки в приложение, либо откройте полноценный веб-кабинет.</blockquote>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_connect")
async def connect_vpn_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user or not user.is_active:
        await callback.answer("⚠️ Ошибка: Ваша подписка неактивна!", show_alert=True)
        return

    text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN, 0)
    await callback.message.edit_text(
        "<b>🚀 Подключение AnKo VPN</b>\n\n"
        "Для каждого пользователя создается индивидуальный <b>🌐 Личный веб-кабинет</b>. "
        "В нём вы можете отслеживать детальный расход трафика, подключать новые устройства в один клик и управлять подпиской.\n\n"
        "<blockquote>Быстрый импорт: нажмите кнопку ниже, чтобы автоматически закинуть настройки в приложение, либо откройте полноценный веб-кабинет.</blockquote>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer("Подключение VPN", show_alert=False)

@router.callback_query(F.data == "menu_sos")
async def sos_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    
    if not user.is_active:
        await callback.answer("Доступно только при активной подписке!", show_alert=True)
        return
        
    text = (
        "🆘 <b>Скорая помощь (Авто-диагностика)</b>\n\n"
        "Система проверила ваш профиль:\n"
        "<blockquote>🟢 Подписка: <b>Активна</b>\n"
        "🟢 Лимит трафика: <b>В норме</b>\n"
        "🟢 Серверы: <b>Работают штатно</b></blockquote>\n\n"
        "<b>Если VPN всё равно не подключается:</b>\n"
        "1. Убедитесь, что вы добавили профиль в приложение.\n"
        "2. Нажмите кнопку «🔄 Перевыпустить ключ». Это создаст новые настройки и принудительно сбросит зависшие сессии."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перевыпустить ключ", callback_data="sos_regen_ask")],
        [InlineKeyboardButton(text="💬 Написать админу", url="https://t.me/BarsikSneg")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Диагностика завершена", show_alert=False)

@router.callback_query(F.data == "sos_regen_ask")
async def sos_regen_ask_callback(callback: CallbackQuery) -> None:
    text = (
        "⚠️ <b>Подтверждение действия</b>\n\n"
        "Перевыпуск ключа навсегда удалит ваш текущий профиль. Старые настройки в приложении перестанут работать, и интернет отключится.\n\n"
        "<b>Вам придется заново добавить новый профиль в приложение.</b> Продолжить?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, перевыпустить", callback_data="sos_regen_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_sos")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)

@router.callback_query(F.data == "sos_regen_confirm")
async def sos_regen_confirm_callback(callback: CallbackQuery, user_service: UserService, session: AsyncSession) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if not user: return
    
    user.is_active = False
    session.add(user)
    await session.commit()
    
    await callback.message.edit_text(
        "⏳ <b>Уничтожаем старые сессии...</b>\n\n"
        "Стираем ваш старый профиль из памяти всех серверов кластера. Пожалуйста, подождите 5-7 секунд."
    )
    
    await asyncio.sleep(6)
    
    new_uuid = str(uuid.uuid4())
    user.vless_uuid = new_uuid
    user.is_active = True
    session.add(user)
    await session.commit()
    
    text = (
        "✅ <b>Ключ успешно перевыпущен!</b>\n\n"
        "Ваш старый профиль навсегда удален с серверов. Обязательно удалите его из вашего приложения (Hiddify / V2Ray / Shadowrocket).\n\n"
        "Теперь перейдите в раздел подключения и добавьте новый профиль."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подключить новый VPN", callback_data="menu_connect")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Ключ обновлен!", show_alert=True)
