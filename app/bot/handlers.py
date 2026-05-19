import uuid

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


router = Router()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Профиль")],
        [KeyboardButton(text="Продлить подписку")],
    ],
    resize_keyboard=True,
)


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession) -> None:
    telegram_id = message.from_user.id
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))

    if user is None:
        user = User(
            telegram_id=telegram_id,
            vless_uuid=uuid.uuid4().hex,
            is_active=False,
        )
        session.add(user)
        await session.commit()

    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=main_keyboard)


@router.message(F.text == "Профиль")
async def profile_handler(message: Message, session: AsyncSession) -> None:
    telegram_id = message.from_user.id
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))

    if user is None:
        await message.answer("Профиль не найден. Нажмите /start для регистрации.", reply_markup=main_keyboard)
        return

    sub_end_date = user.sub_end_date.strftime("%Y-%m-%d %H:%M:%S") if user.sub_end_date else "Не активна"
    profile_text = (
        "Ваш профиль:\n"
        f"ID: {user.telegram_id}\n"
        f"UUID: {user.vless_uuid}\n"
        f"Подписка до: {sub_end_date}"
    )
    await message.answer(profile_text, reply_markup=main_keyboard)


@router.message(F.text == "Продлить подписку")
async def renew_subscription_handler(message: Message) -> None:
    await message.answer("Функция продления подписки будет добавлена в следующих этапах.", reply_markup=main_keyboard)
