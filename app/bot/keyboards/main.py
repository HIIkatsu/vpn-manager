from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Профиль")], [KeyboardButton(text="Продлить подписку")]],
    resize_keyboard=True,
)
