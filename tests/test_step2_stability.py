import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.billing_service import BillingService


class BillingStep2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_reclaim_stale_processing_resets_status(self):
        now = datetime.now(timezone.utc)
        stale_payment = SimpleNamespace(status="processing", processing_started_at=now - timedelta(minutes=10))
        payments = SimpleNamespace(get_stale_processing=AsyncMock(return_value=[stale_payment]))
        session = SimpleNamespace(flush=AsyncMock())

        svc = BillingService(
            session=session,
            users=SimpleNamespace(),
            payments=payments,
            xray_manager=SimpleNamespace(),
            yookassa_service=SimpleNamespace(),
            notifier=SimpleNamespace(),
        )

        recovered = await svc.reclaim_stale_processing()

        self.assertEqual(recovered, 1)
        self.assertEqual(stale_payment.status, "pending")
        self.assertIsNone(stale_payment.processing_started_at)
        session.flush.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
