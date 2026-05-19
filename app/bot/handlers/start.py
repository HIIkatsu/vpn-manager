from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import main_keyboard
from app.services.user_service import UserService

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession, user_service: UserService) -> None:
    await user_service.get_or_create(message.from_user.id)
    await session.commit()
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=main_keyboard)
