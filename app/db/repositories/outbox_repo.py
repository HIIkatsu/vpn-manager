from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app.db.models.outbox_event import OutboxEvent
from app.db.repositories.base import BaseRepository


class OutboxRepository(BaseRepository[OutboxEvent]):
    model = OutboxEvent
    max_attempts = 5

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

    async def claim_pending_batch(self, limit: int) -> list[OutboxEvent]:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(minutes=10)
        await self.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.status == "processing", OutboxEvent.locked_at.is_not(None), OutboxEvent.locked_at <= stale_before)
            .values(status="pending", retry_at=now, locked_at=None)
        )

        stmt = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == "pending",
                or_(OutboxEvent.retry_at.is_(None), OutboxEvent.retry_at <= now),
                OutboxEvent.attempts < self.max_attempts,
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        candidate_ids = list((await self.session.scalars(stmt)).all())
        if not candidate_ids:
            return []

        claimed_ids: list[int] = []
        for event_id in candidate_ids:
            result = await self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.status == "pending",
                    or_(OutboxEvent.retry_at.is_(None), OutboxEvent.retry_at <= now),
                    OutboxEvent.attempts < self.max_attempts,
                )
                .values(
                    status="processing",
                    attempts=OutboxEvent.attempts + 1,
                    locked_at=now,
                    last_error=None,
                )
            )
            if result.rowcount:
                claimed_ids.append(event_id)

        if not claimed_ids:
            return []

        result = await self.session.scalars(select(OutboxEvent).where(OutboxEvent.id.in_(claimed_ids)))
        return list(result.all())

    async def get_pending_batch(self, limit: int) -> list[OutboxEvent]:
        return await self.claim_pending_batch(limit)

    def mark_processed(self, event: OutboxEvent) -> None:
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        event.retry_at = None
        event.locked_at = None
        event.last_error = None

    def mark_failed(self, event: OutboxEvent, error: str) -> None:
        event.last_error = error
        event.locked_at = None
        if event.attempts >= self.max_attempts:
            event.status = "failed"
            event.retry_at = None
            return

        # Exponential backoff capped at five minutes. attempts is incremented when the event is claimed.
        delay_seconds = min(300, 2 ** max(event.attempts - 1, 0) * 5)
        event.status = "pending"
        event.retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
