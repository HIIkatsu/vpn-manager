from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.profile import get_profile_data
from app.bot.keyboards.main import main_inline_keyboard, main_keyboard, os_select_keyboard
from app.core.settings import settings
from app.services.user_service import UserService

router = Router()

WELCOME_TEXT = (
    "<b>Добро пожаловать в AnKo VPN 👋</b>\n\n"
    "Рекомендуемое приложение: <b>Hiddify</b>.\n"
    "Сейчас выберите вашу операционную систему — это нужно для стабильного профиля и корректного fingerprint."
)


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession, user_service: UserService) -> None:
    await user_service.get_or_create(message.from_user.id, message.from_user.username)
    await message.answer(WELCOME_TEXT, reply_markup=os_select_keyboard)
    await message.answer("Главное меню:", reply_markup=main_keyboard)
    await message.answer("Быстрые кнопки:", reply_markup=main_inline_keyboard)


OS_LABELS = {
    "android": "Android",
    "ios": "iOS",
    "windows": "Windows",
    "linux": "Linux",
    "macos": "macOS",
}


@router.callback_query(F.data.startswith("os_"))
async def os_select_callback(callback: CallbackQuery, user_service: UserService) -> None:
    selected_os = callback.data.replace("os_", "", 1)
    if selected_os not in OS_LABELS:
        await callback.answer("Неизвестная ОС", show_alert=True)
        return
    await user_service.set_preferred_os(callback.from_user.id, selected_os)
    await callback.message.answer(
        f"✅ Операционная система сохранена: {OS_LABELS[selected_os]}.\n"
        "Теперь ссылки в профиле автоматически оптимизированы под ваше устройство.\n\n"
        "Нажмите 🚀 Подключить VPN, чтобы открыть deeplink для Hiddify."
    )
    await callback.answer("ОС сохранена")


@router.message(F.text == "🚀 Подключить VPN")
async def connect_vpn_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.")
        return

    if user.is_active:
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
        await message.answer(
            "<b>🚀 Подключить VPN</b>\n"
            "Это быстрый режим подключения: выберите приложение ниже, и deeplink сразу откроет импорт.\n\n"
            "Если хотите посмотреть статус и детали подписки — откройте раздел <b>👤 Профиль</b>.",
            reply_markup=keyboard,
        )
        return

    await message.answer(
        "Подписка сейчас неактивна. Откройте <b>💳 Подписка</b>, чтобы продлить доступ, затем вернитесь сюда для быстрого импорта."
    )


@router.callback_query(F.data == "menu_connect")
async def connect_vpn_callback(callback: CallbackQuery, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("Профиль не найден. Нажмите /start.", show_alert=True)
        return

    if user.is_active:
        text, keyboard = get_profile_data(user, settings.WEBHOOK_URL_DOMAIN)
        await callback.message.answer(
            "<b>🚀 Подключить VPN</b>\n"
            "Это быстрый режим подключения: выберите приложение ниже, и deeplink сразу откроет импорт.\n\n"
            "Если хотите посмотреть статус и детали подписки — откройте раздел <b>👤 Профиль</b>.",
            reply_markup=keyboard,
        )
        await callback.answer()
        return

    await callback.message.answer(
        "Подписка сейчас неактивна. Откройте <b>💳 Подписка</b>, чтобы продлить доступ, затем вернитесь сюда для быстрого импорта."
    )
    await callback.answer()
