import re
from pathlib import Path

p = Path("/root/vpn-manager/app/vpn_admin.py")
text = p.read_text(encoding="utf-8")

# 1. Добавляем импорты и коннект к базе
if "import sqlite3" not in text:
    text = text.replace("import subprocess", "import sqlite3\nimport subprocess")

if "DB_PATH =" not in text:
    db_code = """
DB_PATH = BASE / "config/database.db"

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn
"""
    text = text.replace('LOGIN_RATE_LIMIT = BASE / "admin_login_rate_limit.json"', 'LOGIN_RATE_LIMIT = BASE / "admin_login_rate_limit.json"' + db_code)

# 2. Подменяем чтение юзеров
text = re.sub(
    r'def load_users\(\):.*?return load_json\(USERS\)\.get\("users", \[\]\)',
    '''def load_users():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username as slug, uuid, enabled, token, comment as name FROM users").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []''',
    text,
    flags=re.DOTALL
)

# 3. Подменяем работу с access codes (теперь это token из БД)
text = re.sub(
    r'def ensure_access_codes\(users\):.*?return codes',
    '''def ensure_access_codes(users):
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username, token FROM users").fetchall()
            return {r["username"]: r["token"] for r in rows}
    except Exception:
        return {}''',
    text,
    flags=re.DOTALL
)

text = re.sub(
    r'def load_access_codes\(\):.*?return {}',
    '''def load_access_codes():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username, token FROM users").fetchall()
            return {r["username"]: r["token"] for r in rows}
    except Exception:
        return {}''',
    text,
    flags=re.DOTALL
)

# 4. Обновление токена (rotate_code)
text = re.sub(
    r'def rotate_access_code\(slug\):.*?return code',
    '''def rotate_access_code(slug):
    import secrets
    new_token = secrets.token_hex(16)
    try:
        with get_db() as conn:
            conn.execute("UPDATE users SET token = ? WHERE username = ?", (new_token, slug))
            conn.commit()
    except Exception:
        pass
    return new_token''',
    text,
    flags=re.DOTALL
)

# 5. Локальное включение/отключение
text = re.sub(
    r'def admin_set_user_enabled_local\(slug, enabled\):.*?return True, f"\{slug\}:.*?включён.*?отключён.*?\}", ""',
    '''def admin_set_user_enabled_local(slug, enabled):
    slug = str(slug or "").strip()
    val = 1 if enabled else 0
    with get_db() as conn:
        res = conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (val, slug))
        conn.commit()
        if res.rowcount == 0:
            return False, "Not found", ""
    return True, f"{slug}: {'включён' if enabled else 'отключён'}", ""''',
    text,
    flags=re.DOTALL
)

# 6. Локальное удаление
text = re.sub(
    r'def admin_delete_user_local_no_apply\(slug\):.*?return True, f"\{target\.get\(\'name\', slug\)\} удалён.*?\}", ""',
    '''def admin_delete_user_local_no_apply(slug):
    slug = str(slug or "").strip()
    with get_db() as conn:
        res = conn.execute("DELETE FROM users WHERE username = ?", (slug,))
        conn.commit()
        if res.rowcount == 0:
            return False, "Not found", ""
    return True, f"{slug} удалён", ""''',
    text,
    flags=re.DOTALL
)

# 7. Подменяем формирование ссылок на token вместо slug
text = text.replace(
    'encoded_slug = quote(slug, safe="")\n        link = f"{base_url}/{encoded_slug}.txt"\n        fallback_link = f"{base_url}/{encoded_slug}-8443.txt"',
    'token = str(u.get("token", slug))\n        link = f"{base_url}/{token}.txt"\n        fallback_link = f"{base_url}/{token}.json"'
)

text = text.replace(
    'encoded_slug = quote(str(slug), safe="")\n            text = f"{subscription_base(settings)}/{encoded_slug}.txt"',
    'token = user.get("token", slug)\n            text = f"{subscription_base(settings)}/{token}.txt"'
)

# 8. Исправляем пути к новому CLI генератору
text = text.replace('cmd = ["vpn-manager", "add-user"', 'cmd = ["python3", "/root/vpn-manager/app/vpn-manager.py", "add-user"')
text = text.replace('run(["vpn-manager", "apply"]', 'run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"]')

p.write_text(text, encoding="utf-8")
print("✅ Админка ювелирно пропатчена на SQLite!")
