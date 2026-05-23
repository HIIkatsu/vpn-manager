import unittest

from app.core.security import SharedRateLimiter, WebhookReplayGuard


class SecurityStep2Tests(unittest.TestCase):
    def test_rate_limiter_fail_closed_on_backend_error(self):
        limiter = SharedRateLimiter()

        class FailingRedis:
            def incr(self, key):
                raise Exception("redis down")

        limiter._redis = FailingRedis()
        self.assertFalse(limiter.allow("k", 1, 60, fail_open=False))
        self.assertTrue(limiter.allow("k", 1, 60, fail_open=True))

    def test_replay_guard_blocks_duplicate_event(self):
        guard = WebhookReplayGuard()
        guard._redis = None
        first = guard.mark_if_fresh("evt-1", 120)
        second = guard.mark_if_fresh("evt-1", 120)
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
