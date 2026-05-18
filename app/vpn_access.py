import json
import secrets
from pathlib import Path

BASE = Path("/root/vpn-manager")
USERS = BASE / "users.json"
SETTINGS = BASE / "settings.json"
ACCESS = BASE / "user_access.json"

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def load_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def normalize_code(value):
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def user_path(settings=None):
    if settings is None:
        settings = load_json(SETTINGS)

    sub = settings.get("subscription_path", "vpn")
    if sub.startswith("vpn-"):
        return "vpn-user-" + sub.split("vpn-", 1)[1]
    return sub + "-user"


def common_user_url(settings=None):
    if settings is None:
        settings = load_json(SETTINGS)

    return f"https://{settings['domain']}/{user_path(settings)}/"


def ensure_access_codes(users=None):
    if users is None:
        users = load_json(USERS)["users"]

    if ACCESS.exists():
        access = load_json(ACCESS)
    else:
        access = {}

    used = {normalize_code(v) for v in access.values()}
    changed = False

    for u in users:
        slug = str(u.get("slug", "")).strip()
        if not slug:
            continue

        if slug not in access:
            while True:
                code = "".join(secrets.choice(ALPHABET) for _ in range(7))
                if code not in used:
                    break

            access[slug] = code
            used.add(code)
            changed = True

    if changed or not ACCESS.exists():
        save_json(ACCESS, access)

    return access


def slug_by_code(code, users=None):
    code = normalize_code(code)

    if users is None:
        users = load_json(USERS)["users"]

    access = ensure_access_codes(users)
    enabled_slugs = {str(u.get("slug")) for u in users if u.get("enabled", True)}

    for slug, saved_code in access.items():
        if slug in enabled_slugs and normalize_code(saved_code) == code:
            return slug

    return None
