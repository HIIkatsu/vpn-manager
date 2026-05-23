from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models.outbox_event import OutboxEvent
from app.db.repositories.base import BaseRepository


class OutboxRepository(BaseRepository[OutboxEvent]):
    model = OutboxEvent

    async def enqueue(self, *, event_type: str, aggregate_type: str, aggregate_id: str, dedup_key: str, payload_json: str) -> OutboxEvent:
        existing = await self.session.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key))
        if existing:
            return existing
        event = OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            dedup_key=dedup_key,
            payload_json=payload_json,
            status="pending",
        )
        return await self.add(event)

    async def get_pending_batch(self, limit: int) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    def mark_processed(self, event: OutboxEvent) -> None:
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        event.last_error = None

    def mark_failed(self, event: OutboxEvent, error: str) -> None:
        event.attempts += 1
        event.last_error = error
