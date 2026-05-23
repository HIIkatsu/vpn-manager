file_path = "app/db/models.py"
with open(file_path, "r", encoding="utf-8") as f:
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
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(new_model)
    print("✅ Модель PendingAction добавлена в код (app/db/models.py)")
else:
    print("✅ Модель PendingAction уже есть в коде")
