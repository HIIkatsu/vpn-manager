import hmac
import time
from hashlib import sha256
from ipaddress import ip_address, ip_network
from threading import Lock

from app.core.settings import settings

try:
    from redis import Redis
    from redis.exceptions import RedisError
except Exception:  # pragma: no cover - optional dependency in tests
    Redis = None  # type: ignore[assignment]
    RedisError = Exception  # type: ignore[assignment]


class SharedRateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._memory_hits: dict[str, list[float]] = {}
        self._redis = None
        if settings.REDIS_URL and Redis is not None:
            self._redis = Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    def _allow_memory(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        edge = now - window_seconds
        with self._lock:
            bucket = [ts for ts in self._memory_hits.get(key, []) if ts > edge]
            if len(bucket) >= limit:
                self._memory_hits[key] = bucket
                return False
            bucket.append(now)
            self._memory_hits[key] = bucket
            return True

    def allow(self, key: str, limit: int, window_seconds: int, *, fail_open: bool | None = None) -> bool:
        fail_open = settings.RATE_LIMIT_FAIL_OPEN if fail_open is None else fail_open
        if self._redis is None:
            return self._allow_memory(key, limit, window_seconds)

        redis_key = f"ratelimit:{key}:{int(time.time() // window_seconds)}"
        try:
            current = self._redis.incr(redis_key)
            if current == 1:
                self._redis.expire(redis_key, window_seconds)
            return current <= limit
        except RedisError:
            if fail_open:
                return True
            return False


class WebhookReplayGuard:
    def __init__(self) -> None:
        self._lock = Lock()
        self._memory_seen: dict[str, float] = {}
        self._redis = None
        if settings.REDIS_URL and Redis is not None:
            self._redis = Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    def _mark_memory(self, event_id: str, ttl_seconds: int) -> bool:
        now = time.time()
        edge = now - ttl_seconds
        with self._lock:
            expired = [event for event, ts in self._memory_seen.items() if ts <= edge]
            for event in expired:
                self._memory_seen.pop(event, None)
            if event_id in self._memory_seen:
                return False
            self._memory_seen[event_id] = now
            return True

    def mark_if_fresh(self, event_id: str, ttl_seconds: int) -> bool:
        if self._redis is None:
            return self._mark_memory(event_id, ttl_seconds)
        try:
            key = f"webhook:replay:{event_id}"
            result = self._redis.set(key, "1", ex=ttl_seconds, nx=True)
            return bool(result)
        except RedisError:
            return False


class DistributedLock:
    def __init__(self) -> None:
        self._lock = Lock()
        self._memory_locks: dict[str, float] = {}
        self._redis = None
        if settings.REDIS_URL and Redis is not None:
            self._redis = Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    def acquire(self, key: str, ttl_seconds: int) -> bool:
        if self._redis is not None:
            try:
                return bool(self._redis.set(f"lock:{key}", "1", ex=ttl_seconds, nx=True))
            except RedisError:
                return False

        now = time.time()
        with self._lock:
            exp = self._memory_locks.get(key)
            if exp is not None and exp > now:
                return False
            self._memory_locks[key] = now + ttl_seconds
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
