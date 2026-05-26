from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Подключить VPN")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Подписка")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)

main_inline_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подключить VPN", callback_data="menu_connect")],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="💳 Подписка", callback_data="menu_subscription"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help"),
        ],
    ]
)

os_select_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 Android", callback_data="os_android"),
            InlineKeyboardButton(text="🍎 iOS", callback_data="os_ios"),
        ],
        [
            InlineKeyboardButton(text="🪟 Windows", callback_data="os_windows"),
            InlineKeyboardButton(text="🐧 Linux", callback_data="os_linux"),
        ],
        [InlineKeyboardButton(text="💻 macOS", callback_data="os_macos")],
    ]
)
