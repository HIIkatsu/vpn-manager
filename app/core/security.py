import hmac
from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address, ip_network
from threading import Lock


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            bucket = self._buckets[key]
            edge = now - window_seconds
            while bucket and bucket[0] <= edge:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


def sign_subscription_token(user_uuid: str, expires_at: int, secret: str) -> str:
    payload = f"{user_uuid}:{expires_at}"
    return hmac.new(secret.encode(), payload.encode(), sha256).hexdigest()


def verify_subscription_token(user_uuid: str, expires_at: int, signature: str, secret: str) -> bool:
    expected = sign_subscription_token(user_uuid, expires_at, secret)
    return hmac.compare_digest(expected, signature)


def ip_in_allowlist(ip: str, cidrs: list[str]) -> bool:
    if not cidrs:
        return True
    addr = ip_address(ip)
    return any(addr in ip_network(cidr.strip()) for cidr in cidrs if cidr.strip())
