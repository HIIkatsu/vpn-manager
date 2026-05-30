import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

OLD_BOT_TOKEN = "8697479362:AAHL7jO5lehd8ARyiMH0yCxsm6IBoWZIfxk"
NEW_BOT_USERNAME = "AnKoVPN_bot"

bot = Bot(token=OLD_BOT_TOKEN)
dp = Dispatcher()

kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚀 Перейти в новый бот", url=f"https://t.me/{NEW_BOT_USERNAME}")]
])
text = (
    "⚠️ <b>Этот бот больше не поддерживается.</b>\n\n"
    "Мы переехали на новую высокоскоростную платформу. Твоя подписка сохранена. Перейди в нового бота, чтобы управлять настройками."
)

@dp.message()
async def catch_all_msg(msg: Message):
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query()
async def catch_all_cb(cb: CallbackQuery):
    try:
        await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await cb.answer()

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
