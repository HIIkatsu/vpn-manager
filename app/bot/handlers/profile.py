from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards.main import main_keyboard
from app.services.user_service import UserService
from app.services.xray_manager import XrayManager

router = Router()


@router.message(F.text == "Профиль")
async def profile_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.", reply_markup=main_keyboard)
        return

    sub_end_date = user.sub_end_date.strftime("%Y-%m-%d %H:%M:%S") if user.sub_end_date else "Не активна"
    profile_text = f"Ваш профиль:\nID: {user.telegram_id}\nUUID: {user.vless_uuid}\nПодписка до: {sub_end_date}"
    if user.is_active:
        profile_text += f"\nVLESS ссылка:\n{XrayManager().generate_vless_link(user.vless_uuid)}"
    await message.answer(profile_text, reply_markup=main_keyboard)
