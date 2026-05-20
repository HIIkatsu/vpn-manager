from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Подключить VPN")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Подписка")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)
