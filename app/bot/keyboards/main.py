from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Подключить VPN")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Подписка")],
        [KeyboardButton(text="📖 Инструкция"), KeyboardButton(text="💬 Поддержка")],
    ],
    resize_keyboard=True,
    is_persistent=True
)

main_inline_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Быстрое подключение", callback_data="menu_connect")],
        [
            InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile"),
            InlineKeyboardButton(text="💳 Тарифы и Оплата", callback_data="menu_subscription"),
        ],
        [
            InlineKeyboardButton(text="📡 Состояние сети", callback_data="menu_status"),
            InlineKeyboardButton(text="🆘 Не работает VPN", callback_data="menu_sos"),
        ],
        [
            InlineKeyboardButton(text="📖 Инструкция", url="https://neurosmmai.ru/setup"),
            InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/BarsikSneg")
        ]
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
            InlineKeyboardButton(text="💻 macOS", callback_data="os_macos"),
        ],
        # Теперь эта кнопка вызывает отдельный хендлер, а не просто "назад"
        [InlineKeyboardButton(text="⏭ Пропустить / В главное меню", callback_data="skip_os_select")],
    ]
)
