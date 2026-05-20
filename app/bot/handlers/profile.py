from aiogram import F, Router
from aiogram.types import Message
from aiogram.enums import ParseMode

from app.bot.keyboards.main import main_keyboard
from app.services.user_service import UserService
from app.core.settings import settings

router = Router()

@router.message(F.text == "Профиль")
async def profile_handler(message: Message, user_service: UserService) -> None:
    user = await user_service.get_by_telegram_id(message.from_user.id)
    
    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.", reply_markup=main_keyboard)
        return
        
    sub_end_date = user.sub_end_date.strftime("%Y-%m-%d %H:%M:%S") if user.sub_end_date else "Нет активной подписки"
    
    profile_text = (
        f"👤 <b>Ваш профиль:</b>\n"
        f"ID: <code>{user.telegram_id}</code>\n"
        f"📅 Подписка до: <b>{sub_end_date}</b>\n"
    )
    
    if user.is_active:
        sub_url = f"https://{settings.WEBHOOK_URL_DOMAIN}/webhook/sub/{user.vless_uuid}"
        profile_text += (
            f"\n🌐 <b>URL Подписки</b> (нажмите, чтобы скопировать):\n"
            f"<code>{sub_url}</code>\n\n"
            f"<i>💡 Скопируйте ссылку, вставьте её в раздел «Подписки» или «Группы» в Nekobox/v2rayNG и нажмите «Обновить». Появятся 3 профиля.</i>"
        )
        
    await message.answer(profile_text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard)
