import asyncio

from app.bot.core import bot, dp
from app.bot.handlers import router as _router  # explicit import to guarantee handler registration


def _ensure_handlers_loaded() -> None:
    # Import side effect safety: keep a hard reference so lints/import optimizers
    # do not accidentally strip router import in runtime entrypoint.
    _ = _router


async def run_bot_polling() -> None:
    _ensure_handlers_loaded()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main() -> None:
    try:
        asyncio.run(run_bot_polling())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
