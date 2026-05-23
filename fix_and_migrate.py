import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.settings import settings

def fix():
    # 1. Удаляем мусор из user_repo.py
    bad_file = "app/db/repositories/user_repo.py"
    if os.path.exists(bad_file):
        with open(bad_file, "r", encoding="utf-8") as f:
            content = f.read()
        idx = content.find("from sqlalchemy import JSON, ForeignKey")
        if idx != -1:
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write(content[:idx].strip() + "\n")
            print("✅ Ошибка в user_repo.py вычищена")

    # 2. Ищем РЕАЛЬНЫЙ файл с моделями
    target = None
    for root, dirs, files in os.walk('app'):
        for file in files:
            if file.endswith('.py'):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                    if '__tablename__ = "users"' in text or "__tablename__ = 'users'" in text:
                        target = path
                        break
        if target: break

    if not target:
        print("❌ Не нашел файл с таблицей users!")
        return None

    print(f"✅ Настоящая модель найдена в: {target}")

    with open(target, "r", encoding="utf-8") as f:
        code = f.read()
        
    new_model = """
from sqlalchemy import JSON, ForeignKey, Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

class PendingAction(Base):
    __tablename__ = "pending_actions"
    id = Column(Integer, primary_key=True)
    action_type = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    user = relationship("User", foreign_keys=[user_id])
"""
    if "class PendingAction" not in code:
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n" + new_model)
        print("✅ Модель PendingAction добавлена в правильный файл!")

    return target

async def migrate(target):
    try:
        from app.db.database import Base
    except ImportError:
        try:
            from app.db.models import Base
        except ImportError:
            print("❌ Не смог импортировать Base для миграции")
            return

    # Динамически импортируем найденный файл, чтобы модели привязались к Base
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("real_models", target)
    real_models = importlib.util.module_from_spec(spec)
    sys.modules["real_models"] = real_models
    spec.loader.exec_module(real_models)

    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Таблица pending_actions 100% создана в БД!")

target = fix()
if target:
    asyncio.run(migrate(target))
