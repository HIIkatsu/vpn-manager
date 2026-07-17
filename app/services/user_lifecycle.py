from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PendingAction, User
from app.db.repositories.outbox_repo import OutboxRepository
import uuid
import json


async def delete_user_with_relations(session: AsyncSession, user: User) -> None:
    await session.execute(delete(Payment).where(Payment.user_id == user.id))
    await session.execute(delete(PendingAction).where(PendingAction.user_id == user.id))
    
    outbox = OutboxRepository(session)
    await outbox.enqueue(
        event_type="xray.remove_client",
        aggregate_type="user",
        aggregate_id=str(user.telegram_id),
        dedup_key=f"remove_client:{user.telegram_id}:{uuid.uuid4()}",
        payload_json=json.dumps({"telegram_id": user.telegram_id})
    )
    
    await session.delete(user)
