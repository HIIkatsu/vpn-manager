import asyncio

from app.bot.core import bot, dp
from app.bot.handlers import router as _router  # explicit import to guarantee handler registration
from app.services.xray_manager import XrayManager


def _ensure_handlers_loaded() -> None:
    # Import side effect safety: keep a hard reference so lints/import optimizers
    # do not accidentally strip router import in runtime entrypoint.
    _ = _router


async def run_bot_polling() -> None:
    _ensure_handlers_loaded()
    xray_manager = XrayManager()
    await xray_manager.initialize()
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await XrayManager.close_channel()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run_bot_polling())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
