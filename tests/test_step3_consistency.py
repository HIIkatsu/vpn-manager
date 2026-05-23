import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.repositories.outbox_repo import OutboxRepository
from app.services.billing_service import BillingService


class BillingStep3Tests(unittest.IsolatedAsyncioTestCase):
    async def test_activate_payment_enqueues_outbox_once(self):
        payment = SimpleNamespace(
            payment_id="p1",
            user_id=1,
            status="pending",
            amount=100.0,
            processing_started_at=None,
            processed_event_id=None,
        )
        user = SimpleNamespace(id=1, telegram_id=1001, vless_uuid="uuid-1", is_active=False, sub_end_date=None)

        payments = SimpleNamespace(get_by_payment_id_for_update=AsyncMock(return_value=payment))
        users = SimpleNamespace(get_by_id=AsyncMock(return_value=user))
        outbox = SimpleNamespace(enqueue=AsyncMock())
        session = SimpleNamespace(flush=AsyncMock())

        svc = BillingService(
            session=session,
            users=users,
            payments=payments,
            xray_manager=SimpleNamespace(),
            yookassa_service=SimpleNamespace(),
            notifier=SimpleNamespace(),
            outbox=outbox,
        )

        ok = await svc.activate_payment("p1", event_id="evt-1")

        self.assertTrue(ok)
        self.assertEqual(payment.status, "success")
        self.assertEqual(payment.processed_event_id, "evt-1")
        outbox.enqueue.assert_awaited_once()
        kwargs = outbox.enqueue.await_args.kwargs
        self.assertEqual(kwargs["dedup_key"], "xray.add_client:p1")
        payload = json.loads(kwargs["payload_json"])
        self.assertEqual(payload["telegram_id"], 1001)


class OutboxRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mark_failed_increments_attempts(self):
        event = SimpleNamespace(attempts=0, last_error=None)
        repo = OutboxRepository(SimpleNamespace())
        repo.mark_failed(event, "boom")
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.last_error, "boom")


if __name__ == "__main__":
    unittest.main()
