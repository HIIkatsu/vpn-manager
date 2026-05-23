import asyncio

from app.runtime.workers import auto_expiry_loop
from app.services.xray_manager import XrayManager


async def run_workers() -> None:
    xray_manager = XrayManager()
    await xray_manager.initialize()
    try:
        await auto_expiry_loop()
    finally:
        await XrayManager.close_channel()


def main() -> None:
    try:
        asyncio.run(run_workers())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
