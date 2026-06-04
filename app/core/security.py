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


def ensure_redis_available() -> None:
    if Redis is None:
        raise RuntimeError("redis package is required when REDIS_URL is configured")
    if not settings.REDIS_URL:
        raise RuntimeError("REDIS_URL is required in production")
    client = Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        client.ping()
    except RedisError as exc:
        raise RuntimeError("Redis is not reachable") from exc
    finally:
        client.close()


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


def subscription_signing_secret() -> str:
    return settings.SUBSCRIPTION_SIGNING_SECRET or settings.SECRET_PREFIX


def sign_subscription_token(user_uuid: str, expires_at: int, secret: str | None = None) -> str:
    payload = f"{user_uuid}:{expires_at}"
    signing_secret = secret or subscription_signing_secret()
    return hmac.new(signing_secret.encode(), payload.encode(), sha256).hexdigest()


def verify_subscription_token(user_uuid: str, expires_at: int, signature: str, secret: str | None = None) -> bool:
    if expires_at < int(time.time()):
        return False
    expected = sign_subscription_token(user_uuid, expires_at, secret)
    return hmac.compare_digest(expected, signature)


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def client_ip_from_request(request) -> str:
    trusted_proxies = split_csv(settings.TRUSTED_PROXY_IPS)
    peer_ip = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for and peer_ip and ip_in_allowlist(peer_ip, trusted_proxies):
        return forwarded_for.split(",", 1)[0].strip()
    return peer_ip


def ip_in_allowlist(ip: str, cidrs: list[str]) -> bool:
    if not cidrs:
        return True

    try:
        addr = ip_address(ip)
    except ValueError:
        return False

    for cidr in cidrs:
        try:
            if addr in ip_network(cidr.strip(), strict=False):
                return True
        except ValueError:
            return False
    return False
