import unittest
from pathlib import Path
from contextlib import asynccontextmanager


from app.services.transaction import session_scope
from app.services.yookassa_service import YooKassaService


class DummySession:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class SessionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_scope_commits_on_success(self):
        session = DummySession()

        @asynccontextmanager
        async def factory():
            yield session

        async with session_scope(factory):
            pass

        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 0)

    async def test_session_scope_rolls_back_on_error(self):
        session = DummySession()

        @asynccontextmanager
        async def factory():
            yield session

        with self.assertRaisesRegex(RuntimeError, "boom"):
            async with session_scope(factory):
                raise RuntimeError("boom")

        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.rollback_calls, 1)


class YooKassaAuthTests(unittest.TestCase):
    def test_webhook_hmac_auth_accepts_valid_signature(self):
        service = YooKassaService()
        payload = {"event": "payment.succeeded"}
        signature = service._expected_hmac_signature(payload)

        self.assertTrue(service.is_valid_webhook_auth(None, signature, None, payload))


class WebhookRouterAuthTests(unittest.TestCase):
    def test_webhook_does_not_read_token_from_query_params(self):
        source = Path("app/api/routers/billing_router.py").read_text(encoding="utf-8")
        self.assertNotIn('request.query_params.get("token")', source)


if __name__ == "__main__":
    unittest.main()
