import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.security import SharedRateLimiter, ip_in_allowlist
from app.services.billing_service import BillingService


class Step5LoadTests(unittest.TestCase):
    def test_rate_limiter_enforces_window_limit(self):
        limiter = SharedRateLimiter()
        key = f"load-test:{datetime.now(timezone.utc).timestamp()}"
        self.assertTrue(limiter.allow(key, limit=2, window_seconds=60))
        self.assertTrue(limiter.allow(key, limit=2, window_seconds=60))
        self.assertFalse(limiter.allow(key, limit=2, window_seconds=60))


class Step5ChaosTests(unittest.IsolatedAsyncioTestCase):
    async def test_activate_payment_recovers_processing_on_worker_failure(self):
        payment = SimpleNamespace(
            payment_id="p-chaos",
            user_id=22,
            status="pending",
            amount=100.0,
            processing_started_at=None,
            processed_event_id=None,
        )
        payments = SimpleNamespace(get_by_payment_id_for_update=AsyncMock(return_value=payment))
        users = SimpleNamespace(get_by_id=AsyncMock(side_effect=RuntimeError("worker-crash")))
        session = SimpleNamespace(flush=AsyncMock())

        svc = BillingService(
            session=session,
            users=users,
            payments=payments,
            xray_manager=SimpleNamespace(),
            yookassa_service=SimpleNamespace(),
            notifier=SimpleNamespace(),
        )

        with self.assertRaisesRegex(RuntimeError, "worker-crash"):
            await svc.activate_payment("p-chaos", event_id="evt-chaos")

        self.assertEqual(payment.status, "pending")
        self.assertIsNone(payment.processing_started_at)


class Step5SecurityRegressionTests(unittest.TestCase):
    def test_allowlist_rejects_non_whitelisted_ip(self):
        allowlist = ["10.10.0.0/16", "192.168.1.10/32"]
        self.assertTrue(ip_in_allowlist("10.10.7.42", allowlist))
        self.assertFalse(ip_in_allowlist("8.8.8.8", allowlist))


if __name__ == "__main__":
    unittest.main()
