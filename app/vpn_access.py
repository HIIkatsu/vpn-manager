import json
import sqlite3
from pathlib import Path

BASE = Path('/root/vpn-manager')
SETTINGS = BASE / 'settings.json'
DB_PATH = BASE / 'config/database.db'


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_json(path):
    return json.loads(path.read_text())


def normalize_code(value):
    return ''.join(ch for ch in str(value).upper() if ch.isalnum())


def user_path(settings=None):
    if settings is None:
        settings = load_json(SETTINGS)
    sub = settings.get('subscription_path', 'vpn')
    if sub.startswith('vpn-'):
        return 'vpn-user-' + sub.split('vpn-', 1)[1]
    return sub + '-user'


def common_user_url(settings=None):
    if settings is None:
        settings = load_json(SETTINGS)
    return f"https://{settings['domain']}/{user_path(settings)}/"


def slug_by_code(code, users=None):
    normalized = normalize_code(code)
    if not normalized:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE enabled = 1 AND UPPER(TRIM(token)) = ? LIMIT 1",
            (normalized,),
        ).fetchone()
    return row['username'] if row else None
