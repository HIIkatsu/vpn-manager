from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.profile import get_profile_data
from app.bot.keyboards.main import main_inline_keyboard, main_keyboard
from app.core.settings import settings
from app.services.user_service import UserService

router = Router()

WELCOME_TEXT = (
    "<b>Добро пожаловать в AnKo VPN 👋</b>\n\n"
    "Подключение займет 1 минуту:\n"
    "1) Откройте <b>👤 Профиль</b>\n"
    "2) Нажмите кнопку вашего приложения (Hiddify / V2rayTun / Happ)\n"
    "3) Подтвердите импорт и подключитесь\n\n"
    "Если подписка не активна — откройте <b>💳 Подписка</b> и продлите доступ."
)


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession, user_service: UserService) -> None:
    await user_service.get_or_create(message.from_user.id)
#     await session.commit() # FIXED: UoW violation
    await message.answer(WELCOME_TEXT, reply_markup=main_keyboard)
    await message.answer("Или используйте inline-меню:", reply_markup=main_inline_keyboard)


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
