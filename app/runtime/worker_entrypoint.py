import asyncio

from app.runtime.workers import auto_expiry_loop


def main() -> None:
    try:
        asyncio.run(auto_expiry_loop())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
