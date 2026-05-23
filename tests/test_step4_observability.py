import ast
import unittest
from pathlib import Path

from app.core.logging_utils import get_request_id, log_context, set_request_id


class LoggingContextTests(unittest.TestCase):
    def test_log_context_uses_context_request_id_by_default(self):
        set_request_id("ctx-42")
        ctx = log_context(payment_id=7)
        self.assertEqual(ctx["request_id"], "ctx-42")
        self.assertEqual(get_request_id(), "ctx-42")
        self.assertEqual(ctx["payment_id"], 7)

    def test_log_context_includes_observability_contract_fields(self):
        ctx = log_context(
            payment_id="pay-1",
            telegram_id=123,
            event_id="evt-1",
            action_source="billing.webhook",
            attempt=2,
            endpoint="/api/billing/webhook",
        )
        self.assertEqual(ctx["attempt"], 2)
        self.assertEqual(ctx["endpoint"], "/api/billing/webhook")
        self.assertEqual(ctx["event_id"], "evt-1")


class ErrorPathLoggingTests(unittest.TestCase):
    def test_admin_router_has_no_print_calls(self):
        tree = ast.parse(Path("app/api/routers/admin_router.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
        self.assertEqual(calls, [], "print() must not be used in admin error paths")

    def test_xray_manager_uses_log_context(self):
        code = Path("app/services/xray_manager.py").read_text(encoding="utf-8")
        self.assertIn("log_context(", code)

    def test_yookassa_service_uses_log_context(self):
        code = Path("app/services/yookassa_service.py").read_text(encoding="utf-8")
        self.assertIn("log_context(", code)


if __name__ == "__main__":
    unittest.main()
