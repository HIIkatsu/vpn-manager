import re

# 1. Убираем уязвимые дефолты из настроек
settings_path = '/root/vpn-manager-v2/app/core/settings.py'
with open(settings_path, 'r') as f:
    data = f.read()
data = data.replace('ADMIN_USERNAME: str = "admin"', 'ADMIN_USERNAME: str')
data = data.replace('ADMIN_PASSWORD: str = "admin"', 'ADMIN_PASSWORD: str')
with open(settings_path, 'w') as f:
    f.write(data)

# 2. Переписываем Rate Limiter для Gunicorn
sec_path = '/root/vpn-manager-v2/app/core/security.py'
with open(sec_path, 'r') as f:
    data = f.read()

new_limiter = """import sqlite3
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
"""

data = re.sub(r'class InMemoryRateLimiter:.*?return True', new_limiter, data, flags=re.DOTALL)
with open(sec_path, 'w') as f:
    f.write(data)

# 3. Обновляем импорт в роутере биллинга
billing_path = '/root/vpn-manager-v2/app/api/routers/billing_router.py'
with open(billing_path, 'r') as f:
    billing = f.read()
billing = billing.replace('InMemoryRateLimiter', 'SharedRateLimiter')
with open(billing_path, 'w') as f:
    f.write(billing)

print("✅ Финальный аудит-патч успешно применен!")
