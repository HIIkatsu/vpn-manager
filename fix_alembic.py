import asyncio
from sqlalchemy import text
from app.db.database import engine

async def main():
    async with engine.begin() as conn:
        # Расширяем лимит символов в служебной таблице Alembic
        await conn.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128);"))
    print("✅ Таблица alembic_version успешно расширена до 128 символов!")

if __name__ == "__main__":
    asyncio.run(main())
