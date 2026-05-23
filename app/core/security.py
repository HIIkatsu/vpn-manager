import hmac
from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address, ip_network
from threading import Lock


import sqlite3
import time

class SharedRateLimiter:
    def __init__(self) -> None:
        self.db_path = "/tmp/vpn_rate_limit.db"
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS hits (key TEXT, timestamp REAL)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hits_key ON hits(key)")
        except Exception:
            pass

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        edge = now - window_seconds
        try:
            with sqlite3.connect(self.db_path, timeout=2.0) as conn:
                conn.execute("DELETE FROM hits WHERE timestamp <= ?", (edge,))
                cur = conn.execute("SELECT COUNT(*) FROM hits WHERE key = ?", (key,))
                count = cur.fetchone()[0]
                if count >= limit:
                    return False
                conn.execute("INSERT INTO hits (key, timestamp) VALUES (?, ?)", (key, now))
                return True
        except Exception:
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
