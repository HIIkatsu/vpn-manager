#!/usr/bin/env python3
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

BASE = Path("/root/vpn-manager")
USERS = BASE / "users.json"
SETTINGS = BASE / "settings.json"
AUTH = BASE / "auth.json"
ACCESS = BASE / "user_access.json"
PENDING = BASE / "admin_pending_changes.json"
LOGIN_RATE_LIMIT = BASE / "admin_login_rate_limit.json"
DB_PATH = BASE / "config/database.db"

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


HOST = "127.0.0.1"
PORT = 8010

COOKIE_NAME = "vpn_admin_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
COOKIE_PATH = "/vpn-admin/"
VERSION = "admin-old-ui-pending-modal-v6"
LOGIN_WINDOW_SEC = 10 * 60
LOGIN_MAX_ATTEMPTS = 8


def load_json(path: Path):
    return json.loads(path.read_text())


def save_json(path: Path, data):
    mode = 0o600 if path.name in {"auth.json", "user_access.json", "admin_login_rate_limit.json"} else 0o640
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def atomic_write_text(path: Path, text: str, mode=0o640):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def run(cmd, timeout=120):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def shell(cmd, timeout=120):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, shell=True)
    return p.returncode, p.stdout, p.stderr


def esc(value):
    return html.escape(str(value), quote=True)


def js_arg(value):
    """Return a safe JavaScript string literal for inline event handlers."""
    return html.escape(json.dumps(str(value), ensure_ascii=False), quote=True)


def short(value, left=32, right=14):
    value = str(value)
    if len(value) <= left + right + 3:
        return value
    return value[:left] + "…" + value[-right:]


def human_bytes(value):
    value = int(value or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(value)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024


def load_users():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username as slug, uuid, enabled, token, comment as name FROM users").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def load_settings():
    return load_json(SETTINGS)


def load_auth():
    return load_json(AUTH)


def check_password(username, password):
    try:
        data = load_auth()
        if username != data.get("username", "admin"):
            return False

        salt = base64.b64decode(data["salt"])
        expected = base64.b64decode(data["password_hash"])
        iterations = int(data.get("iterations", 220000))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def auth_secret():
    return base64.b64decode(load_auth()["secret"])


def sign(payload):
    sig = hmac.new(auth_secret(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_token(username):
    exp = int(time.time()) + COOKIE_MAX_AGE
    payload = base64.urlsafe_b64encode(f"{username}:{exp}".encode()).decode().rstrip("=")
    return payload + "." + sign(payload)


def verify_token(token):
    if not token or "." not in token:
        return False

    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sign(payload), sig):
        return False

    try:
        padded = payload + "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        username, exp_s = raw.rsplit(":", 1)
        if username != load_auth().get("username", "admin"):
            return False
        return int(exp_s) >= int(time.time())
    except Exception:
        return False


def csrf_token(session_token):
    if not session_token:
        return ""
    sig = hmac.new(auth_secret(), session_token.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def load_rate_limit():
    try:
        data = json.loads(LOGIN_RATE_LIMIT.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_rate_limit(data):
    atomic_write_text(LOGIN_RATE_LIMIT, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=0o600)


def too_many_attempts(ip):
    now_ts = int(time.time())
    data = load_rate_limit()
    attempts = [int(ts) for ts in data.get(ip, []) if now_ts - int(ts) <= LOGIN_WINDOW_SEC]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return True
    return False


def remember_failed_attempt(ip):
    now_ts = int(time.time())
    data = load_rate_limit()
    attempts = [int(ts) for ts in data.get(ip, []) if now_ts - int(ts) <= LOGIN_WINDOW_SEC]
    attempts.append(now_ts)
    data[ip] = attempts[-LOGIN_MAX_ATTEMPTS:]
    save_rate_limit(data)


def clear_attempts(ip):
    data = load_rate_limit()
    if ip in data:
        data.pop(ip, None)
        save_rate_limit(data)


def load_access_codes():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username, token FROM users").fetchall()
            return {r["username"]: r["token"] for r in rows}
    except Exception:
        return {}


def save_access_codes(codes):
    save_json(ACCESS, codes)


def make_access_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(7))
        if not any(bad in code for bad in ("BAD", "XXX", "SEX", "FUK")):
            return code


def ensure_access_codes(users):
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT username, token FROM users").fetchall()
            return {r["username"]: r["token"] for r in rows}
    except Exception:
        return {}


def rotate_access_code(slug):
    import secrets
    new_token = secrets.token_hex(16)
    try:
        with get_db() as conn:
            conn.execute("UPDATE users SET token = ? WHERE username = ?", (new_token, slug))
            conn.commit()
    except Exception:
        pass
    return new_token


def load_pending_changes():
    try:
        if PENDING.exists():
            data = json.loads(PENDING.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("changes", [])
                return data
    except Exception:
        pass
    return {"changes": []}


def save_pending_changes(data):
    atomic_write_text(PENDING, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=0o640)


def mark_pending_change(action, slug=""):
    data = load_pending_changes()
    changes = data.get("changes", [])
    action = str(action or "change")
    slug = str(slug or "")

    # Не плодим одинаковые записи.
    changes = [
        item for item in changes
        if not (str(item.get("action")) == action and str(item.get("slug")) == slug)
    ]

    changes.append({
        "action": action,
        "slug": slug,
        "ts": int(time.time()),
    })

    data["changes"] = changes[-50:]
    data["updated_at"] = int(time.time())
    save_pending_changes(data)


def clear_pending_changes():
    try:
        if PENDING.exists():
            PENDING.unlink()
    except Exception:
        save_pending_changes({"changes": []})


def render_pending_modal_parts(pending, csrf):
    changes = pending.get("changes", [])
    if not changes:
        return "", ""

    labels = {
        "delete": "Удалён профиль",
        "toggle:enable": "Включён профиль",
        "toggle:disable": "Отключён профиль",
        "add": "Добавлен профиль",
        "rotate-code": "Обновлён код",
    }

    items = ""
    for item in reversed(changes):
        action = str(item.get("action", "change"))
        slug = str(item.get("slug", ""))
        items += f'<li><span>{esc(labels.get(action, action))}</span><b>{esc(slug or "—")}</b></li>'

    trigger_html = """
    <button class="pending-compact-btn" type="button" onclick="openPendingModal()">
      Есть несохранённые изменения
    </button>
    """

    modal_html = f"""
<div class="modal" id="pendingModal" onclick="hidePendingModal(event)">
  <div class="modal-card pending-modal-card" onclick="event.stopPropagation()">
    <div class="modal-head">
      <b>Неприменённые изменения</b>
      <button type="button" onclick="closePendingModal()">Закрыть</button>
    </div>
    <p class="pending-modal-note">Админка уже обновлена, но живой Xray применит изменения только после нажатия кнопки ниже.</p>
    <ul class="pending-modal-list">{items}</ul>
    <form method="post" action="apply" class="pending-modal-form">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <button class="primary pending-apply" type="submit">Применить изменения</button>
    </form>
  </div>
</div>
    """
    return trigger_html, modal_html


def public_user_path(settings):
    sub = settings.get("subscription_path", "vpn")
    if sub.startswith("vpn-"):
        return "vpn-user-" + sub.split("vpn-", 1)[1]
    return sub + "-user"


def public_user_url(settings):
    return f"https://{settings['domain']}/{public_user_path(settings)}/"


def public_user_invite_url(settings):
    return f"{public_user_url(settings)}?invite=1"


def subscription_base(settings):
    return f"https://{settings['domain']}/{settings['subscription_path']}"


def status():
    x_code, x_out, _ = run(["systemctl", "is-active", "xray"], timeout=15)
    n_code, n_out, _ = run(["systemctl", "is-active", "nginx"], timeout=15)
    admin_code, _, _ = shell("ss -lntp | grep -q ':8010'", timeout=15)
    api_code, _, _ = shell("ss -lntp | grep -q ':10085'", timeout=15)
    public443_code, _, _ = shell("ss -lntp | grep -q ':443'", timeout=15)
    xray8443_code, _, _ = shell("ss -lntp | grep -q ':8443'", timeout=15)

    return {
        "xray": x_out.strip() or "unknown",
        "nginx": n_out.strip() or "unknown",
        "admin": "online" if admin_code == 0 else "off",
        "api": "enabled" if api_code == 0 else "off",
        "public443": "listening" if public443_code == 0 else "closed",
        "xray8443": "listening" if xray8443_code == 0 else "closed",
        "xray_ok": x_code == 0 and x_out.strip() == "active",
        "nginx_ok": n_code == 0 and n_out.strip() == "active",
        "admin_ok": admin_code == 0,
        "api_ok": api_code == 0,
        "public443_ok": public443_code == 0,
        "xray8443_ok": xray8443_code == 0,
    }


def parse_xray_stats(raw):
    result = {}

    try:
        data = json.loads(raw)
        stats_list = data.get("stat") or data.get("stats") or []
        for item in stats_list:
            name = item.get("name", "")
            value = int(item.get("value", 0))
            m = re.match(r"user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
            if m:
                slug, direction = m.groups()
                result.setdefault(slug, {"uplink": 0, "downlink": 0})
                result[slug][direction] += value
        if result:
            return result
    except Exception:
        pass

    for m in re.finditer(r'user>>>([^">]+)>>>traffic>>>(uplink|downlink).*?value:\s*(\d+)', raw, re.S):
        slug, direction, value = m.group(1), m.group(2), int(m.group(3))
        result.setdefault(slug, {"uplink": 0, "downlink": 0})
        result[slug][direction] += value

    name_matches = list(re.finditer(r'name:\s*"([^"]+)"', raw))
    for nm in name_matches:
        name = nm.group(1)
        m = re.match(r"user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue

        chunk = raw[nm.end(): nm.end() + 400]
        vm = re.search(r"value:\s*(\d+)", chunk)
        if not vm:
            continue

        slug, direction = m.groups()
        result.setdefault(slug, {"uplink": 0, "downlink": 0})
        result[slug][direction] += int(vm.group(1))

    return result


def xray_stats():
    commands = [
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", "user>>>"],
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", "user"],
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085"],
    ]

    last_raw = ""
    api_reachable = False
    for cmd in commands:
        code, out, err = run(cmd, timeout=15)
        raw = (out + "\n" + err).strip()
        last_raw = "$ " + " ".join(cmd) + "\n" + raw

        if code == 0:
            api_reachable = True
            parsed = parse_xray_stats(raw)
            if parsed:
                return parsed, True, last_raw

    return {}, api_reachable, last_raw


def journal_activity(minutes=30):
    cmd = ["journalctl", "-u", "xray", f"--since={minutes} minutes ago", "--no-pager", "-o", "short-iso"]
    code, out, err = run(cmd, timeout=20)
    raw = out + "\n" + err
    data = {}

    if code != 0:
        return data

    for line in raw.splitlines():
        m = re.search(r"email:\s*(\S+)", line)
        if not m:
            continue

        slug = m.group(1)
        data.setdefault(slug, {"connections": 0, "last": "—"})
        data[slug]["connections"] += 1

        ts = line.split()[0] if line.split() else ""
        if "T" in ts:
            ts = ts.split("T", 1)[1][:5]
        data[slug]["last"] = ts or "—"

    return data


def badge(ok, text):
    cls = "ok" if ok else "bad"
    text_map = {
        "active": "Работает",
        "online": "Доступен",
        "enabled": "Доступен",
        "off": "Отключен",
        "closed": "Закрыт",
        "listening": "Слушает",
    }
    label = text_map.get(str(text).strip().lower(), text)
    return f'<span class="badge {cls}">{esc(label)}</span>'


def qr_svg(text):
    code, out, err = run(["qrencode", "-t", "SVG", "-o", "-", text], timeout=20)
    if code == 0 and out.strip():
        return out.encode("utf-8")

    fallback = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">
  <rect width="100%" height="100%" fill="white"/>
  <text x="20" y="120" font-size="16" fill="black">QR error</text>
</svg>"""
    return fallback.encode("utf-8")


STYLE = """
<style>
:root{
  color-scheme:dark;
  --bg:#070b12;
  --text:#eef3ff;
  --muted:#8d98ad;
  --line:rgba(255,255,255,.12);
  --line2:rgba(255,255,255,.18);
  --ok:#56e0aa;
  --bad:#ff6b7a;
  --accent:#8ab4ff;
  --accent2:#42e3c8;
}
*{
  box-sizing:border-box;
  -webkit-tap-highlight-color:transparent;
}
body{
  margin:0;
  font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:
    radial-gradient(circle at 18% 0%, rgba(89,131,255,.25), transparent 30rem),
    radial-gradient(circle at 92% 8%, rgba(86,224,170,.13), transparent 32rem),
    radial-gradient(circle at 50% 100%, rgba(255,255,255,.05), transparent 32rem),
    var(--bg);
  color:var(--text);
}
.wrap{
  max-width:1180px;
  margin:0 auto;
  padding:16px 14px 70px;
}
.hero{
  margin:0 0 12px;
  padding:0;
}
.hero-inner{
  width:100%;
  display:flex;
  justify-content:space-between;
  gap:10px;
  align-items:flex-start;
}
h1{
  margin:0;
  font-size:clamp(32px,7vw,54px);
  line-height:.98;
  letter-spacing:-.075em;
}
.subtitle{
  color:var(--muted);
  margin-top:7px;
  font-size:15px;
}
.hero-actions{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  justify-content:flex-end;
}
.hero-stack{
  display:flex;
  flex-direction:column;
  gap:12px;
}
.hero-admin{
  margin-bottom:14px;
  padding:18px;
  background:linear-gradient(145deg, rgba(255,255,255,.09), rgba(255,255,255,.045));
  border:1px solid var(--line);
  border-radius:28px;
  box-shadow:0 24px 70px rgba(0,0,0,.28);
  backdrop-filter:blur(20px);
}
.hero-admin .hero-inner{
  flex-wrap:nowrap;
  align-items:flex-start;
}
.hero-admin .hero-actions{
  margin-left:auto;
  justify-content:flex-end;
  flex:0 0 auto;
}
.logout-mini{
  padding:10px 14px !important;
  border-radius:14px !important;
  font-size:14px;
  font-weight:800;
  color:var(--text) !important;
  background:rgba(255,255,255,.09) !important;
  border:1px solid var(--line) !important;
}
.pending-compact-btn{
  width:100%;
  padding:10px 14px !important;
  border-radius:13px !important;
  white-space:normal;
  background:linear-gradient(135deg, rgba(255,209,102,.24), rgba(255,209,102,.10)) !important;
  border-color:rgba(255,209,102,.42) !important;
  color:#fff1c7 !important;
  box-shadow:0 0 34px rgba(255,209,102,.10);
}
button,a.button-link{
  border:1px solid var(--line);
  background:rgba(255,255,255,.09);
  color:var(--text);
  border-radius:15px;
  padding:11px 13px;
  font-weight:850;
  cursor:pointer;
  text-decoration:none;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  text-align:center;
  transition:.15s ease;
}
button:hover,a.button-link:hover{
  background:rgba(255,255,255,.14);
  transform:translateY(-1px);
}
button:active,a.button-link:active{
  transform:scale(.98);
}
button.primary,a.primary{
  background:linear-gradient(135deg, rgba(138,180,255,.28), rgba(138,180,255,.13));
  border-color:rgba(138,180,255,.38);
}
button.danger{
  background:rgba(255,107,122,.13);
  border-color:rgba(255,107,122,.36);
  color:#ffd9df;
}
.status-strip{
  display:grid;
  grid-template-columns:repeat(6,minmax(0,1fr));
  gap:8px;
  margin-bottom:12px;
}
.card,.panel,.user-card{
  background:linear-gradient(145deg, rgba(255,255,255,.088), rgba(255,255,255,.045));
  border:1px solid var(--line);
  border-radius:24px;
  box-shadow:0 24px 70px rgba(0,0,0,.28);
  backdrop-filter:blur(20px);
}
.card{
  padding:13px;
  min-height:72px;
  display:flex;
  justify-content:space-between;
  gap:8px;
  align-items:center;
}
.card-title{
  color:var(--muted);
  font-size:12px;
  margin-bottom:6px;
}
.badge{
  display:inline-flex;
  align-items:center;
  width:max-content;
  padding:6px 10px;
  border-radius:999px;
  font-weight:900;
  font-size:13px;
}
.badge.ok{
  color:var(--ok);
  background:rgba(86,224,170,.13);
}
.badge.bad{
  color:var(--bad);
  background:rgba(255,107,122,.13);
}
.panel{
  padding:16px;
  margin-bottom:14px;
}
.panel-title{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:14px;
}
h2{
  margin:0;
  font-size:clamp(26px,6vw,40px);
  letter-spacing:-.055em;
}
input,select{
  background:rgba(0,0,0,.30);
  color:var(--text);
  border:1px solid var(--line);
  border-radius:15px;
  padding:12px 13px;
  outline:none;
  min-height:44px;
}
input:focus,select:focus{
  border-color:rgba(138,180,255,.7);
}
.form-row{
  display:grid;
  grid-template-columns:1fr 1fr auto;
  gap:10px;
}
.toolbar{
  display:flex;
  gap:10px;
  flex-wrap:wrap;
  margin-bottom:14px;
}
.toolbar input{
  flex:1;
  min-width:220px;
}
.users-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:12px;
}
.user-card{
  padding:15px;
  overflow:hidden;
}
.user-head{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:flex-start;
  margin-bottom:12px;
}
.user-id{
  display:flex;
  gap:10px;
  align-items:center;
  min-width:0;
}
.avatar{
  width:42px;
  height:42px;
  border-radius:14px;
  display:grid;
  place-items:center;
  background:rgba(255,255,255,.09);
  border:1px solid var(--line);
  font-size:24px;
  flex:0 0 auto;
}
.user-title{
  font-weight:950;
  font-size:23px;
  letter-spacing:-.04em;
}
.muted{
  color:var(--muted);
  font-size:13px;
}
.live{
  color:var(--ok);
}
.idle{
  color:var(--muted);
}
.access-panel{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin:12px 0;
  padding:14px;
  border-radius:20px;
  background:linear-gradient(135deg, rgba(95,143,255,.16), rgba(66,227,200,.10));
  border:1px solid rgba(138,180,255,.20);
}
.access-label{
  color:var(--muted);
  font-size:12px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.08em;
}
.access-code{
  margin-top:4px;
  font-size:15px;
  font-weight:800;
  word-break:break-all;
  line-height:1.3;
  letter-spacing:normal;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--text);
}
.copy-mini{
  min-width:116px;
  background:rgba(255,255,255,.10);
}
.mini-grid{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:8px;
}
.mini-grid div{
  padding:10px;
  border-radius:16px;
  background:rgba(0,0,0,.18);
  border:1px solid rgba(255,255,255,.07);
}
.mini-grid span{
  display:block;
  color:var(--muted);
  font-size:12px;
}
.mini-grid b{
  display:block;
  margin-top:4px;
  font-size:15px;
}
.bar{
  height:9px;
  border-radius:999px;
  overflow:hidden;
  background:rgba(255,255,255,.08);
  margin:11px 0 8px;
}
.bar i{
  display:block;
  height:100%;
  border-radius:999px;
  background:linear-gradient(90deg,#5f8fff,#42e3c8);
}
.traffic-split{
  display:flex;
  justify-content:space-between;
  gap:10px;
  color:var(--muted);
  font-size:13px;
}
.share-box{
  margin-top:12px;
  padding:13px;
  border-radius:19px;
  background:rgba(0,0,0,.17);
  border:1px solid rgba(255,255,255,.07);
}
.share-title{
  font-weight:900;
  font-size:16px;
}
.share-preview{
  margin-top:4px;
  color:var(--muted);
  font-size:13px;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.share-actions{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:8px;
  margin-top:10px;
}
.user-actions{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:8px;
  margin-top:12px;
}
.user-actions form{
  margin:0;
}
.user-actions form button{
  width:100%;
}
.details{
  margin-top:12px;
  border-radius:18px;
  background:rgba(0,0,0,.16);
  border:1px solid rgba(255,255,255,.08);
  padding:12px;
}
.details summary{
  cursor:pointer;
  font-weight:900;
  color:var(--accent2);
}
.kv{
  display:grid;
  grid-template-columns:90px minmax(0,1fr);
  gap:10px;
  margin-top:10px;
  align-items:center;
}
.kv span{
  color:var(--muted);
  font-size:13px;
}
code{
  color:#dfe8ff;
  font-size:12px;
  word-break:break-all;
}
.link{
  width:100%;
  margin-top:10px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12px;
}
.msg{
  margin-bottom:14px;
  padding:12px 14px;
  border-radius:16px;
  background:rgba(86,224,170,.12);
  border:1px solid rgba(86,224,170,.25);
}
.log-details{
  margin-bottom:14px;
  border-radius:18px;
  background:rgba(0,0,0,.2);
  border:1px solid var(--line);
  padding:12px;
}
pre{
  white-space:pre-wrap;
  overflow:auto;
  max-height:340px;
  padding:14px;
  border-radius:18px;
  background:rgba(0,0,0,.35);
  border:1px solid var(--line);
}
.hidden{
  display:none!important;
}
.modal{
  position:fixed;
  inset:0;
  z-index:100;
  display:none;
  align-items:center;
  justify-content:center;
  padding:18px;
  background:rgba(2,6,14,.72);
  backdrop-filter:blur(18px);
}
.modal.show{
  display:flex;
}
.modal-card{
  width:min(420px,100%);
  border-radius:30px;
  padding:18px;
  background:linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.055));
  border:1px solid var(--line2);
  box-shadow:0 28px 90px rgba(0,0,0,.46);
}
.modal-head{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:center;
  margin-bottom:14px;
}
.modal img{
  display:block;
  width:100%;
  background:#fff;
  border-radius:22px;
  padding:14px;
}
.toast{
  position:fixed;
  left:50%;
  bottom:20px;
  transform:translateX(-50%) translateY(20px);
  opacity:0;
  background:rgba(15,23,42,.96);
  border:1px solid var(--line2);
  color:var(--text);
  padding:12px 14px;
  border-radius:16px;
  box-shadow:0 18px 50px rgba(0,0,0,.35);
  z-index:100;
  transition:.2s ease;
}
.toast.show{
  opacity:1;
  transform:translateX(-50%) translateY(0);
}
.login-wrap{
  min-height:100vh;
  display:grid;
  place-items:center;
  padding:18px;
}
.login-card{
  width:min(440px,100%);
  border:1px solid var(--line);
  border-radius:28px;
  padding:24px;
  background:linear-gradient(145deg, rgba(255,255,255,.10), rgba(255,255,255,.045));
  box-shadow:0 24px 70px rgba(0,0,0,.34);
  backdrop-filter:blur(20px);
}
.login-card h1{
  margin:0 0 8px;
  font-size:42px;
}
.login-card p{
  color:var(--muted);
  margin:0 0 20px;
}
.login-card input{
  width:100%;
  margin-bottom:10px;
  min-height:48px;
  font-size:16px;
}
.login-card button{
  width:100%;
  min-height:48px;
}
.login-error{
  margin-bottom:12px;
  color:#ffd9df;
  background:rgba(255,107,122,.13);
  border:1px solid rgba(255,107,122,.35);
  border-radius:16px;
  padding:12px;
}
@media(max-width:920px){
  .status-strip{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .users-grid{
    grid-template-columns:1fr;
  }
  .form-row{
    grid-template-columns:1fr;
  }
}
@media(max-width:620px){
  .hero-inner{
    flex-direction:column;
  }
  .hero-admin .hero-inner{
    flex-direction:column;
    align-items:flex-start;
  }
  .hero-admin .hero-actions{
    width:100%;
    justify-content:flex-start;
    margin-left:0;
  }
  .logout-mini{
    align-self:flex-end;
  }
  .share-actions{
    grid-template-columns:1fr;
  }
  .user-actions{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .access-panel{
    align-items:stretch;
    flex-direction:column;
  }
  .copy-mini{
    width:100%;
  }
  .mini-grid{
    grid-template-columns:1fr;
  }
}

/* Admin UI V5 polish */
.server-dock{
  margin-bottom:14px;
  padding:16px;
  border-radius:28px;
  background:
    radial-gradient(circle at 0% 0%, rgba(95,143,255,.18), transparent 24rem),
    radial-gradient(circle at 100% 0%, rgba(66,227,200,.12), transparent 24rem),
    linear-gradient(145deg, rgba(255,255,255,.095), rgba(255,255,255,.045));
  border:1px solid rgba(255,255,255,.13);
  box-shadow:0 24px 70px rgba(0,0,0,.26);
  backdrop-filter:blur(20px);
}
.server-dock-head{
  display:flex;
  justify-content:space-between;
  gap:16px;
  align-items:flex-start;
  margin-bottom:14px;
}
.dock-eyebrow{
  color:rgba(255,255,255,.48);
  font-size:12px;
  font-weight:900;
  letter-spacing:.10em;
  text-transform:uppercase;
}
.dock-title{
  margin-top:4px;
  font-size:26px;
  line-height:1;
  font-weight:950;
  letter-spacing:-.05em;
}
.dock-sub{
  margin-top:7px;
  color:var(--muted);
  font-size:14px;
}
.dock-traffic{
  min-width:150px;
  padding:14px 16px;
  border-radius:22px;
  background:rgba(0,0,0,.18);
  border:1px solid rgba(255,255,255,.07);
  text-align:right;
}
.dock-traffic span{
  display:block;
  color:var(--muted);
  font-size:13px;
}
.dock-traffic b{
  display:block;
  margin-top:4px;
  font-size:27px;
  letter-spacing:-.04em;
}
.service-line{
  display:flex;
  gap:8px;
  overflow-x:auto;
  padding-bottom:2px;
}
.service-line::-webkit-scrollbar{
  display:none;
}
.service-pill{
  min-width:max-content;
  display:flex;
  align-items:center;
  gap:8px;
  padding:9px 10px;
  border-radius:999px;
  background:rgba(0,0,0,.16);
  border:1px solid rgba(255,255,255,.07);
}
.service-pill span{
  color:var(--muted);
  font-size:13px;
  font-weight:750;
}
.service-pill .badge{
  padding:5px 8px;
  font-size:12px;
}

.user-card-v5{
  padding:16px;
}
.user-top-v5{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:flex-start;
  margin-bottom:12px;
}
.user-main-v5{
  display:grid;
  grid-template-columns:.9fr 1.1fr;
  gap:10px;
}
.code-card-v5,
.traffic-card-v5,
.user-share-v5{
  border-radius:22px;
  background:rgba(0,0,0,.16);
  border:1px solid rgba(255,255,255,.07);
}
.code-card-v5{
  padding:15px;
  background:
    radial-gradient(circle at 100% 0%, rgba(66,227,200,.10), transparent 18rem),
    linear-gradient(145deg, rgba(95,143,255,.15), rgba(255,255,255,.035));
}
.code-card-v5 button{
  width:100%;
  margin-top:12px;
  min-height:44px;
}
.traffic-card-v5{
  padding:15px;
}
.traffic-top-v5{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:flex-start;
}
.traffic-big{
  margin-top:4px;
  font-size:25px;
  font-weight:950;
  letter-spacing:-.04em;
}
.user-share-v5{
  margin-top:10px;
  padding:14px;
  display:grid;
  grid-template-columns:1fr auto;
  gap:12px;
  align-items:center;
}
.share-actions-v5{
  display:flex;
  gap:8px;
  align-items:center;
}
.share-actions-v5 button,
.share-actions-v5 a{
  min-height:44px;
  min-width:98px;
}
.tech-v5{
  margin-top:10px;
}
.admin-actions-v5{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:8px;
  margin-top:10px;
}
.admin-actions-v5 form{
  margin:0;
}
.admin-actions-v5 button{
  width:100%;
  min-height:46px;
}
@media(max-width:720px){
  .server-dock-head{
    flex-direction:column;
  }
  .dock-traffic{
    width:100%;
    text-align:left;
  }
  .user-main-v5{
    grid-template-columns:1fr;
  }
  .user-share-v5{
    grid-template-columns:1fr;
  }
  .share-actions-v5{
    display:grid;
    grid-template-columns:1fr 1fr;
  }
  .admin-actions-v5{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
}


/* Admin UI V5.1 fixes */
.user-card.user-card-v5{
  background:
    radial-gradient(circle at 100% 0%, rgba(66,227,200,.06), transparent 24rem),
    radial-gradient(circle at 0% 0%, rgba(95,143,255,.08), transparent 22rem),
    linear-gradient(145deg, rgba(255,255,255,.068), rgba(255,255,255,.030));
  border:1px solid rgba(255,255,255,.10);
}

.user-card-v5,
.user-card-v5 *{
  box-sizing:border-box;
}

.user-share-v5{
  width:100%;
  min-width:0;
  overflow:hidden;
  grid-template-columns:minmax(0,1fr) auto;
  align-items:center;
}

.user-share-v5 > div:first-child{
  min-width:0;
  max-width:100%;
}

.user-share-v5 .share-title{
  min-width:0;
}

.user-share-v5 .share-preview{
  display:block;
  min-width:0;
  max-width:100%;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

.share-actions-v5{
  min-width:0;
  flex-wrap:wrap;
  justify-content:flex-end;
  align-items:center;
}

.share-actions-v5 > *{
  box-sizing:border-box;
  max-width:100%;
}

.share-actions-v5 button,
.share-actions-v5 a{
  min-width:110px;
  max-width:100%;
}

@media(max-width:720px){
  .user-share-v5{
    grid-template-columns:1fr;
    align-items:stretch;
  }

  .share-actions-v5{
    width:100%;
    display:grid;
    grid-template-columns:repeat(2, minmax(0,1fr));
    gap:8px;
  }

  .share-actions-v5 button,
  .share-actions-v5 a{
    width:100%;
    min-width:0;
  }
}


/* Delete button polish */
.user-card form{
  margin:0;
  min-width:0;
}

.user-card form button{
  width:100%;
}

.delete-btn{
  background:rgba(255,107,122,.13) !important;
  border-color:rgba(255,107,122,.38) !important;
  color:#ffd9df !important;
}


/* admin action polish */
a, button {
  -webkit-tap-highlight-color: transparent;
}

.delete-btn {
  background: linear-gradient(135deg, rgba(255,106,130,.16), rgba(255,106,130,.09)) !important;
  border: 1px solid rgba(255,106,130,.34) !important;
  color: #ffdce2 !important;
}

.disable-btn {
  background: linear-gradient(135deg, rgba(255,196,92,.12), rgba(255,196,92,.06)) !important;
  border: 1px solid rgba(255,196,92,.22) !important;
  color: #ffe4a6 !important;
}

.delete-btn:hover,
.disable-btn:hover {
  filter: brightness(1.04);
}



/* final action color polish */
a, button {
  -webkit-tap-highlight-color: transparent;
}

button.delete-btn,
.delete-btn {
  background: linear-gradient(135deg, rgba(255,92,122,.17), rgba(255,92,122,.08)) !important;
  border-color: rgba(255,92,122,.34) !important;
  color: #ffdce3 !important;
}

button.disable-btn,
.disable-btn {
  background: linear-gradient(135deg, rgba(255,198,92,.13), rgba(255,198,92,.055)) !important;
  border-color: rgba(255,198,92,.24) !important;
  color: #ffe7ad !important;
}

button.delete-btn:hover,
button.disable-btn:hover {
  filter: brightness(1.05);
}



/* final admin actions polish */
a, button {
  -webkit-tap-highlight-color: transparent;
}

#qrModal.show,
#qrModal.open {
  display: flex !important;
  opacity: 1 !important;
  pointer-events: auto !important;
}

#qrModal img,
#qrImg {
  display: block;
  max-width: 100%;
}

.admin-actions-v5 .delete-btn {
  background: linear-gradient(135deg, rgba(255,92,122,.16), rgba(255,92,122,.075)) !important;
  border-color: rgba(255,92,122,.34) !important;
  color: #ffdce3 !important;
}

.admin-actions-v5 form:last-child button.danger {
  background: linear-gradient(135deg, rgba(255,197,92,.13), rgba(255,197,92,.055)) !important;
  border-color: rgba(255,197,92,.25) !important;
  color: #ffe4a8 !important;
}


/* ADMIN CLEAN FINAL CSS */
.admin-actions-v5 .delete-btn,
.admin-actions-v5 button.danger,
.admin-actions-v5 form button.danger {
  background: linear-gradient(135deg, rgba(255,92,122,.15), rgba(255,92,122,.065)) !important;
  border: 1px solid rgba(255,92,122,.30) !important;
  color: #ffdce2 !important;
}

.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 button.danger:hover,
.admin-actions-v5 form button.danger:hover {
  background: linear-gradient(135deg, rgba(255,92,122,.21), rgba(255,92,122,.095)) !important;
}


/* Clean final action fixes */
.admin-actions-v5 a,
.admin-actions-v5 button{
  width:100%;
  min-height:46px;
}

.admin-actions-v5 .delete-btn,
.admin-actions-v5 .disable-btn,
.admin-actions-v5 button.danger{
  background:linear-gradient(135deg, rgba(255,92,122,.145), rgba(255,92,122,.060)) !important;
  border-color:rgba(255,92,122,.32) !important;
  color:#ffdce3 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08);
}

.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 .disable-btn:hover,
.admin-actions-v5 button.danger:hover{
  background:linear-gradient(135deg, rgba(255,92,122,.18), rgba(255,92,122,.075)) !important;
  filter:none !important;
}


/* Final muted red danger actions */
.admin-actions-v5 .delete-btn,
.admin-actions-v5 .disable-btn,
.admin-actions-v5 button.danger{
  background:linear-gradient(135deg, rgba(255,86,115,.135), rgba(255,86,115,.055)) !important;
  border:1px solid rgba(255,86,115,.30) !important;
  color:#ffdbe1 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.07), 0 10px 26px rgba(0,0,0,.10) !important;
}

.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 .disable-btn:hover,
.admin-actions-v5 button.danger:hover{
  background:linear-gradient(135deg, rgba(255,86,115,.17), rgba(255,86,115,.07)) !important;
  border-color:rgba(255,86,115,.38) !important;
  filter:none !important;
}


/* FINAL_DANGER_BUTTONS_REAL */
.admin-actions-v5 .delete-btn,
.admin-actions-v5 .disable-btn,
.admin-actions-v5 form button.danger,
.admin-actions-v5 form:last-child button.danger{
  background:linear-gradient(135deg, rgba(255,86,115,.145), rgba(255,86,115,.060)) !important;
  border:1px solid rgba(255,86,115,.34) !important;
  color:#ffdce3 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.07), 0 10px 26px rgba(0,0,0,.12) !important;
  filter:none !important;
}

.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 .disable-btn:hover,
.admin-actions-v5 form button.danger:hover,
.admin-actions-v5 form:last-child button.danger:hover{
  background:linear-gradient(135deg, rgba(255,86,115,.18), rgba(255,86,115,.075)) !important;
  border-color:rgba(255,86,115,.42) !important;
  color:#ffe5ea !important;
  filter:none !important;
}


/* FINAL_DANGER_BUTTONS_SAME_COLOR */
.admin-actions-v5 .delete-btn,
.admin-actions-v5 .disable-btn,
.admin-actions-v5 button.danger,
.admin-actions-v5 form:last-child button.danger {
  background:linear-gradient(135deg, rgba(255,86,115,.145), rgba(255,86,115,.060)) !important;
  border:1px solid rgba(255,86,115,.34) !important;
  color:#ffdce3 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.07), 0 10px 26px rgba(0,0,0,.12) !important;
  filter:none !important;
}

.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 .disable-btn:hover,
.admin-actions-v5 button.danger:hover,
.admin-actions-v5 form:last-child button.danger:hover {
  background:linear-gradient(135deg, rgba(255,86,115,.18), rgba(255,86,115,.075)) !important;
  border-color:rgba(255,86,115,.42) !important;
  color:#ffe5ea !important;
}


/* FINAL_SAME_MUTED_RED_BUTTONS */
.admin-actions-v5 .delete-btn,
.admin-actions-v5 .disable-btn,
.admin-actions-v5 button.danger,
.admin-actions-v5 form:last-child button.danger {
  background:linear-gradient(135deg, rgba(255,86,115,.145), rgba(255,86,115,.060)) !important;
  border:1px solid rgba(255,86,115,.34) !important;
  color:#ffdce3 !important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.07), 0 10px 26px rgba(0,0,0,.12) !important;
  filter:none !important;
}
.admin-actions-v5 .delete-btn:hover,
.admin-actions-v5 .disable-btn:hover,
.admin-actions-v5 button.danger:hover,
.admin-actions-v5 form:last-child button.danger:hover {
  background:linear-gradient(135deg, rgba(255,86,115,.18), rgba(255,86,115,.075)) !important;
  border-color:rgba(255,86,115,.42) !important;
  color:#ffe5ea !important;
}


/* FINAL_REAL_MUTED_RED_DANGER */
html body .admin-actions-v5 button.delete-btn,
html body .admin-actions-v5 button.disable-btn,
html body .admin-actions-v5 button.muted-red-action,
html body .admin-actions-v5 form:last-child button.danger {
  background: linear-gradient(135deg, rgba(255,86,115,.145), rgba(255,86,115,.060)) !important;
  border: 1px solid rgba(255,86,115,.34) !important;
  color: #ffdce3 !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.07), 0 10px 26px rgba(0,0,0,.12) !important;
  filter: none !important;
}

html body .admin-actions-v5 button.delete-btn:hover,
html body .admin-actions-v5 button.disable-btn:hover,
html body .admin-actions-v5 button.muted-red-action:hover,
html body .admin-actions-v5 form:last-child button.danger:hover {
  background: linear-gradient(135deg, rgba(255,86,115,.18), rgba(255,86,115,.075)) !important;
  border-color: rgba(255,86,115,.42) !important;
  color: #ffe5ea !important;
  filter: none !important;
}


/* PENDING_CHANGES_INDICATOR */
.pending-box{
  margin-bottom:14px;
  padding:14px 16px;
  border-radius:24px;
  border:1px solid rgba(255,255,255,.10);
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  box-shadow:0 18px 54px rgba(0,0,0,.20);
  backdrop-filter:blur(18px);
}
.pending-box b{
  display:block;
  font-size:17px;
  letter-spacing:-.02em;
}
.pending-box span{
  display:block;
  margin-top:4px;
  color:var(--muted);
  font-size:13px;
  line-height:1.35;
}
.pending-box.clean{
  background:linear-gradient(135deg, rgba(86,224,170,.09), rgba(255,255,255,.035));
  border-color:rgba(86,224,170,.18);
}
.pending-box.clean b{
  color:#b9ffe8;
}
.pending-box.dirty{
  background:
    radial-gradient(circle at 0% 0%, rgba(255,209,102,.16), transparent 22rem),
    linear-gradient(135deg, rgba(255,209,102,.105), rgba(255,255,255,.035));
  border-color:rgba(255,209,102,.25);
}
.pending-box.dirty b{
  color:#ffe7a8;
}
.pending-list{
  display:flex;
  flex-wrap:wrap;
  gap:7px;
  margin-top:10px;
}
.pending-list span{
  margin:0;
  padding:6px 9px;
  border-radius:999px;
  background:rgba(0,0,0,.18);
  border:1px solid rgba(255,255,255,.07);
  color:rgba(255,255,255,.72);
  font-size:12px;
}
.pending-list span b{
  display:inline;
  font-size:12px;
  color:#fff;
}
.pending-apply{
  background:linear-gradient(135deg, rgba(255,209,102,.24), rgba(255,209,102,.10)) !important;
  border-color:rgba(255,209,102,.42) !important;
  color:#fff1c7 !important;
  box-shadow:0 0 34px rgba(255,209,102,.10);
}
.pending-modal-card{
  width:min(620px,100%);
}
.pending-modal-note{
  margin:0 0 10px;
  color:var(--muted);
  font-size:14px;
  line-height:1.4;
}
.pending-modal-list{
  list-style:none;
  margin:0 0 14px;
  padding:0;
  display:flex;
  flex-direction:column;
  gap:8px;
  max-height:42vh;
  overflow:auto;
}
.pending-modal-list li{
  display:flex;
  justify-content:space-between;
  gap:8px;
  padding:8px 10px;
  border-radius:12px;
  background:rgba(255,255,255,.04);
  border:1px solid rgba(255,255,255,.08);
  font-size:13px;
}
.pending-modal-list li span{
  color:rgba(255,255,255,.78);
}
.pending-modal-list li b{
  color:#fff3cc;
  text-align:right;
  word-break:break-word;
}
.pending-modal-form{
  display:flex;
  justify-content:flex-end;
}

</style>
"""


JS = """
<script>
function showToast(text){
  const el = document.getElementById('toast');
  if(!el) return;
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.remove('show'), 1800);
}

function selectVisibleText(id){
  const el = document.getElementById(id);
  if(!el) return false;
  try{
    el.removeAttribute('readonly');
    el.focus();
    el.select();
    el.setSelectionRange(0, el.value.length);
    el.setAttribute('readonly', 'readonly');
    return true;
  }catch(e){
    try{ el.focus(); el.select(); return true; }catch(_){ return false; }
  }
}

async function copyText(text, visibleTextareaId){
  text = text || '';
  let selected = false;
  if(visibleTextareaId){
    selected = selectVisibleText(visibleTextareaId);
  }
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(text);
      showToast('Скопировано');
      return true;
    }
  }catch(e){}

  try{
    let area = visibleTextareaId ? document.getElementById(visibleTextareaId) : null;
    let temporary = false;
    if(!area){
      area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.left = '-9999px';
      area.style.top = '0';
      document.body.appendChild(area);
      temporary = true;
    }
    area.focus();
    area.select();
    area.setSelectionRange(0, area.value.length);
    const ok = document.execCommand('copy');
    if(temporary) area.remove();
    if(ok){
      showToast('Скопировано');
      return true;
    }
  }catch(e){}

  if(!selected && visibleTextareaId){
    selectVisibleText(visibleTextareaId);
  }
  showToast('Автокопирование заблокировано. Текст выделен — скопируй вручную.');
  return false;
}

function filterUsers(){
  const search = document.getElementById('search');
  const filter = document.getElementById('stateFilter');
  const q = search ? search.value.toLowerCase().trim() : '';
  const state = filter ? filter.value : 'all';

  document.querySelectorAll('.user-card').forEach(card => {
    const mt = !q || card.dataset.name.includes(q) || card.dataset.slug.includes(q);
    const ms = state === 'all' || card.dataset.state === state;
    card.classList.toggle('hidden', !(mt && ms));
  });
}

window.showQr = function(slug, name, kind){
  const modal = document.getElementById('qrModal');
  const title = document.getElementById('qrTitle');
  const img = document.getElementById('qrImg');

  if(!modal || !img){
    return true;
  }

  if(title){
    title.textContent = 'QR · ' + (name || slug || 'profile');
  }

  const q = new URLSearchParams();
  q.set('slug', slug || '');
  q.set('kind', kind || 'vpn');
  q.set('t', Date.now().toString());

  img.src = 'qr?' + q.toString();
  modal.classList.add('show');
  modal.style.display = 'flex';
  return false;
};

window.closeQr = function(){
  const modal = document.getElementById('qrModal');
  if(!modal) return;
  modal.classList.remove('show');
  modal.style.display = 'none';
};

window.hideQr = function(e){
  if(!e || e.target.id === 'qrModal'){
    window.closeQr();
  }
};

window.openPendingModal = function(){
  const modal = document.getElementById('pendingModal');
  if(!modal) return;
  modal.classList.add('show');
  modal.style.display = 'flex';
};

window.closePendingModal = function(){
  const modal = document.getElementById('pendingModal');
  if(!modal) return;
  modal.classList.remove('show');
  modal.style.display = 'none';
};

window.hidePendingModal = function(e){
  if(!e || e.target.id === 'pendingModal'){
    window.closePendingModal();
  }
};

function confirmDelete(name){
  return confirm('Удалить профиль "' + name + '" полностью?\\n\\nБудет удалён пользователь, код входа, подписки и доступ в Xray.');
}

document.addEventListener('keydown', e => {
  if(e.key === 'Escape'){
    window.closeQr();
    window.closePendingModal();
  }
});

document.addEventListener('click', function(e){
  const qr = e.target.closest('.qr-admin-btn');
  if(qr){
    const opened = window.showQr(qr.dataset.qrSlug || '', qr.dataset.qrName || '', 'vpn');
    if(opened === false){
      e.preventDefault();
      e.stopPropagation();
    }
  }
});

document.addEventListener('DOMContentLoaded', function(){
  document.querySelectorAll('.user-card button, .user-card a').forEach(function(el){
    const t = (el.textContent || '').trim();
    if(t === 'Удалить') el.classList.add('delete-btn');
    if(t === 'Отключить') el.classList.add('disable-btn');
  });
});

</script>
"""


def render_login(error=""):
    error_html = f'<div class="login-error">{esc(error)}</div>' if error else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN Admin Login</title>
{STYLE}
</head>
<body>
<div class="login-wrap">
  <form class="login-card" method="post" action="login">
    <h1>🇳🇱 VPN Admin</h1>
    <p>Вход сохранится на 30 дней в этом браузере.</p>
    {error_html}
    <input name="username" placeholder="Логин" value="admin" autocomplete="username" required>
    <input name="password" placeholder="Пароль" type="password" autocomplete="current-password" required>
    <button class="primary" type="submit">Войти</button>
  </form>
</div>
</body>
</html>"""


def render_invite_page(user, invite_text, user_page_url, access_code):
    name = str(user.get("name", user.get("slug", "VPN")))
    slug = str(user.get("slug", ""))
    safe_invite = esc(invite_text)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Инвайт · {esc(name)}</title>
{STYLE}
<style>
.invite-page{{
  max-width:760px;
  margin:0 auto;
  padding:28px 18px 48px;
}}
.invite-textarea{{
  width:100%;
  min-height:220px;
  resize:vertical;
  border:1px solid rgba(255,255,255,.12);
  border-radius:24px;
  background:rgba(255,255,255,.045);
  color:var(--text);
  font:600 18px/1.45 inherit;
  padding:18px;
  outline:none;
  box-sizing:border-box;
  white-space:pre-wrap;
}}
.invite-actions{{
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  margin-top:14px;
}}
.invite-hint{{
  margin-top:12px;
  color:var(--muted);
  font-size:14px;
  line-height:1.45;
}}
</style>
</head>
<body>
<div class="invite-page">
  <header class="hero" style="margin-bottom:18px">
    <div class="hero-inner">
      <div>
        <h1>Инвайт</h1>
        <div class="subtitle">{esc(name)} · {esc(slug)}</div>
      </div>
      <div class="hero-actions">
        <a class="button-link" href="./">Назад</a>
      </div>
    </div>
  </header>

  <section class="panel">
    <div class="panel-title"><h2>Текст для отправки</h2></div>
    <textarea class="invite-textarea" id="inviteText" readonly onclick="selectVisibleText('inviteText')">{safe_invite}</textarea>
    <div class="invite-actions">
      <button class="primary" type="button" onclick="copyText(document.getElementById('inviteText').value, 'inviteText')">Скопировать инвайт</button>
      <a class="button-link" href="{esc(user_page_url)}" target="_blank" rel="noopener">Открыть страница</a>
    </div>
    <div class="invite-hint">Если браузер блокирует автокопирование, кнопка хотя бы выделит текст. После этого нажми системную кнопку «Копировать».</div>
  </section>

  <section class="panel">
    <div class="panel-title"><h2>Данные</h2></div>
    <div class="kv"><span>Страница</span><code>{esc(user_page_url)}</code></div>
    <div class="kv"><span>Код</span><code>{esc(access_code)}</code></div>
  </section>
</div>
<div class="toast" id="toast"></div>
{JS}
</body>
</html>"""


def render(message="", log="", csrf=""):
    users = load_users()
    settings = load_settings()
    access_codes = ensure_access_codes(users)
    st = status()
    pending = load_pending_changes()
    pending_count = len(pending.get("changes", []))
    pending_button_html, pending_modal_html = render_pending_modal_parts(pending, csrf)

    base_url = subscription_base(settings)
    user_page_url = public_user_invite_url(settings)

    stats, stats_ok, stats_raw = xray_stats()
    activity = journal_activity(30)

    total_users = len(users)
    active_users = sum(1 for u in users if u.get("enabled", True))
    all_total = 0
    total_connections = 0
    max_total = 1

    totals = {}
    for u in users:
        slug = str(u.get("slug", ""))
        up = stats.get(slug, {}).get("uplink", 0)
        down = stats.get(slug, {}).get("downlink", 0)
        total = up + down
        conns = activity.get(slug, {}).get("connections", 0)
        last = activity.get(slug, {}).get("last", "—")

        totals[slug] = {
            "up": up,
            "down": down,
            "total": total,
            "connections": conns,
            "last": last,
        }

        all_total += total
        total_connections += conns
        max_total = max(max_total, total)

    cards = ""

    for u in users:
        enabled = bool(u.get("enabled", True))
        slug = str(u.get("slug", ""))
        name = str(u.get("name", slug))
        uuid = str(u.get("uuid", ""))

        token = str(u.get("token", slug))
        link = f"{base_url}/{token}.txt"
        fallback_link = f"{base_url}/{token}.json"

        action = "disable" if enabled else "enable"
        label = "Отключить" if enabled else "Включить"
        btn_class = "danger disable-btn" if enabled else "primary"

        traffic = totals.get(slug, {"up": 0, "down": 0, "total": 0, "connections": 0, "last": "—"})
        percent = int((traffic["total"] / max_total) * 100) if max_total and traffic["total"] else 0
        percent = max(0, min(percent, 100))

        active_mark = "live" if traffic["connections"] else "idle"
        access_code = access_codes.get(slug, "—")

        masked_page = short(user_page_url, 36, 16)
        masked_sub = short(link, 36, 16)

        invite_text = (
            f"🔐 VPN доступ — {name}\n\n"
            f"Страница подключения:\n{user_page_url}\n\n"
            f"Код доступа: {access_code}\n\n"
            "Если ссылка не открылась, откройте её в браузере вручную.\n"
        )

        cards += f"""
        <article class="user-card user-card-v5" data-name="{esc(name).lower()}" data-slug="{esc(slug).lower()}" data-state="{'on' if enabled else 'off'}">
            <div class="user-top-v5">
                <div class="user-id">
                    <div class="avatar">🇳🇱</div>
                    <div>
                        <div class="user-title">{esc(name)}</div>
                        <div class="muted">{esc(slug)} · <span class="{active_mark}">{'активен' if traffic['connections'] else 'тихо'}</span></div>
                    </div>
                </div>
                {badge(enabled, 'ON' if enabled else 'OFF')}
            </div>

            <div class="user-main-v5">
                <div class="code-card-v5">
                    <div class="access-label">Код входа</div>
                    <div class="access-code">{esc(access_code)}</div>
                    <button type="button" onclick="copyText({js_arg(access_code)})">Скопировать код</button>
                </div>

                <div class="traffic-card-v5">
                    <div class="traffic-top-v5">
                        <div>
                            <div class="access-label">Расход трафика</div>
                            <div class="traffic-big">{esc(human_bytes(traffic['total']))}</div>
                        </div>
                        <div class="muted">30 мин · {traffic['connections']} conn</div>
                    </div>
                    <div class="bar"><i style="width:{percent}%"></i></div>
                    <div class="traffic-split">
                        <span>↓ {esc(human_bytes(traffic['down']))}</span>
                        <span>↑ {esc(human_bytes(traffic['up']))}</span>
                        <span>{esc(traffic['last'])}</span>
                    </div>
                </div>
            </div>

            <div class="user-share-v5">
                <div>
                    <div class="share-title">Страница подключения</div>
                    <div class="share-preview">{esc(masked_page)}</div>
                </div>
                <div class="share-actions-v5">
                    <a class="button-link primary" href="{esc(user_page_url)}" target="_blank" rel="noopener">Открыть</a>
                    <a class="button-link" href="invite?slug={esc(slug)}" target="_blank" rel="noopener">Инвайт</a>
                </div>
            </div>

            <details class="details tech-v5">
              <summary>Технические данные</summary>
              <div class="kv"><span>UUID</span><code>{esc(uuid)}</code></div>
              <div class="kv"><span>Подписка</span><code>{esc(masked_sub)}</code></div>
              <input class="link" readonly value="{esc(link)}" onclick="this.select()">
              <input class="link" readonly value="{esc(fallback_link)}" onclick="this.select()">
            </details>

            <div class="admin-actions-v5">
                <a class="button-link qr-admin-btn" href="qr?slug={esc(slug)}&kind=vpn" target="_blank" rel="noopener" data-qr-slug="{esc(slug)}" data-qr-name="{esc(name)}">QR</a>
                
<form method="post" action="./delete-user">
  <input type="hidden" name="csrf" value="{esc(csrf)}">
  <input type="hidden" name="slug" value="{esc(slug)}">
  <button
    class="danger delete-btn muted-red-action"
    type="button"
    data-delete-slug="{esc(slug)}"
    data-delete-name="{esc(name)}"
    onclick="event.preventDefault();event.stopImmediatePropagation();const n=this.dataset.deleteName||'профиль';if(confirm('Удалить профиль «'+n+'» полностью?')){{HTMLFormElement.prototype.submit.call(this.form);}}return false;"
  >Удалить</button>
</form>
                <form method="post" action="rotate-code">
                    <input type="hidden" name="csrf" value="{esc(csrf)}">
                    <input type="hidden" name="slug" value="{esc(slug)}">
                    <button type="submit">Новый код</button>
                </form>
                <form method="post" action="toggle">
                    <input type="hidden" name="csrf" value="{esc(csrf)}">
                    <input type="hidden" name="slug" value="{esc(slug)}">
                    <input type="hidden" name="action" value="{esc(action)}">
                    <button class="{btn_class}" type="submit">{label}</button>
                </form>
            </div>
        </article>
        """

    message_html = f'<div class="msg">{esc(message)}</div>' if message else ""
    log_html = f"""
    <details class="log-details" open>
      <summary>Лог операции</summary>
      <pre>{esc(log)}</pre>
    </details>
    """ if log else ""

    stats_note = "" if stats_ok else f"""
    <details class="log-details">
      <summary>Stats API пока не отдал byte-счётчики. Используется активность из journalctl.</summary>
      <pre>{esc(stats_raw)}</pre>
    </details>
    """

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN Admin</title>
{STYLE}
</head>
<body>
<div class="wrap">
  <header class="hero hero-admin">
    <div class="hero-stack">
      <div class="hero-inner">
        <div>
          <h1>🇳🇱 VPN Admin</h1>
          <div class="subtitle">Пользователи, коды доступа, подписки и состояние сервера</div>
        </div>
        <div class="hero-actions">
          <a class="button-link logout-mini" href="logout">Выйти</a>
        </div>
      </div>
      {pending_button_html}
    </div>
  </header>

  {message_html}
  {log_html}

  <section class="server-dock">
    <div class="server-dock-head">
      <div>
        <div class="dock-eyebrow">Состояние сервера</div>
        <div class="dock-title">NeuroSMM VPN работает</div>
        <div class="dock-sub">{active_users}/{total_users} пользователей включено · {total_connections} соединений за 30 минут</div>
      </div>
      <div class="dock-traffic">
        <span>Трафик</span>
        <b>{esc(human_bytes(all_total))}</b>
      </div>
    </div>

    <div class="service-line">
      <div class="service-pill">
        <span>Xray</span>
        {badge(st["xray_ok"], st["xray"])}
      </div>
      <div class="service-pill">
        <span>Nginx</span>
        {badge(st["nginx_ok"], st["nginx"])}
      </div>
      <div class="service-pill">
        <span>Публичный вход</span>
        {badge(st["public443_ok"], "443")}
      </div>
      <div class="service-pill">
        <span>Резерв</span>
        {badge(st["xray8443_ok"], "8443")}
      </div>
      <div class="service-pill">
        <span>Stats API</span>
        {badge(st["api_ok"], st["api"])}
      </div>
    </div>
  </section>

  {stats_note}

  <section class="panel">
    <div class="panel-title"><h2>Добавить пользователя</h2></div>
    <form class="form-row" method="post" action="add">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <input name="name" placeholder="Имя, например Ivan" required>
      <input name="slug" placeholder="slug, можно пустым">
      <button class="primary" type="submit">Создать локально</button>
    </form>
    <div class="muted" style="margin-top:10px">Код входа создастся автоматически и появится в карточке.</div>
  </section>

  <section class="panel">
    <div class="panel-title">
      <h2>Пользователи</h2>
      <div class="muted">{active_users}/{total_users} включено · {total_connections} conn за 30 мин</div>
    </div>
    <div class="toolbar">
      <input id="search" placeholder="Поиск по имени или slug" oninput="filterUsers()">
      <select id="stateFilter" onchange="filterUsers()">
        <option value="all">Все</option>
        <option value="on">Только ON</option>
        <option value="off">Только OFF</option>
      </select>
    </div>
    <div class="users-grid" id="usersGrid">{cards}</div>
  </section>
</div>

<div class="modal" id="qrModal" onclick="hideQr(event)">
  <div class="modal-card" onclick="event.stopPropagation()">
    <div class="modal-head">
      <b id="qrTitle">QR</b>
      <button type="button" onclick="closeQr()">Закрыть</button>
    </div>
    <img id="qrImg" alt="QR">
  </div>
</div>

{pending_modal_html}

<div class="toast" id="toast"></div>
{JS}
</body>
</html>"""


def admin_delete_user_everywhere(slug):
    slug = str(slug or "").strip()
    if not slug:
        return False, "slug пустой", ""

    old_users_text = USERS.read_text()
    old_users_data = json.loads(old_users_text)
    old_users = old_users_data.get("users", [])

    target = None
    new_users = []
    for u in old_users:
        if str(u.get("slug")) == slug:
            target = u
        else:
            new_users.append(u)

    if not target:
        return False, f"Пользователь {slug} не найден", ""

    old_access_text = ""
    access_data = {}
    try:
        if ACCESS.exists():
            old_access_text = ACCESS.read_text()
            access_data = json.loads(old_access_text)
    except Exception:
        access_data = {}

    new_access_data = dict(access_data)
    new_access_data.pop(slug, None)

    USERS.write_text(json.dumps({"users": new_users}, ensure_ascii=False, indent=2) + "\n")

    try:
        if "ACCESS" in globals():
            ACCESS.write_text(json.dumps(new_access_data, ensure_ascii=False, indent=2) + "\n")
    except Exception:
        pass

    code, out, err = run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)

    if code != 0:
        USERS.write_text(old_users_text)
        try:
            if old_access_text:
                ACCESS.write_text(old_access_text)
        except Exception:
            pass

        rollback_code, rollback_out, rollback_err = run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
        log = out + err + "\n\nROLLBACK:\n" + rollback_out + rollback_err
        return False, f"Удаление {slug} отменено: apply упал, конфиг восстановлен", log

    try:
        settings = load_json(SETTINGS)
        subdir = Path(settings.get("subscription_dir", ""))
        if subdir.exists():
            for suffix in (".txt", "-443.txt", "-8443.txt"):
                f = subdir / f"{slug}{suffix}"
                if f.exists():
                    f.unlink()
    except Exception as e:
        out += f"\nПользователь удалён, но старые файлы подписки не удалось подчистить: {e}\n"

    return True, f"{target.get('name', slug)} удалён", out + err


def admin_delete_user_now(slug):
    slug = str(slug or "").strip()

    if not slug:
        return False, "slug пустой", ""

    if "/" in slug or "\\" in slug or "\x00" in slug:
        return False, "Некорректный slug", ""

    access_path = globals().get("ACCESS", BASE / "user_access.json")

    old_users_text = USERS.read_text(encoding="utf-8")
    old_access_text = access_path.read_text(encoding="utf-8") if access_path.exists() else "{}\n"

    try:
        users_data = json.loads(old_users_text)
    except Exception as e:
        return False, "Не удалось прочитать users.json", str(e)

    users = users_data.get("users", [])
    target = None
    kept = []

    for u in users:
        if str(u.get("slug", "")) == slug:
            target = u
        else:
            kept.append(u)

    if not target:
        return False, f"Пользователь {slug} не найден", ""

    try:
        access = json.loads(old_access_text) if old_access_text.strip() else {}
    except Exception:
        access = {}

    access.pop(slug, None)
    access.pop(str(target.get("name", "")), None)

    USERS.write_text(
        json.dumps({"users": kept}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    access_path.write_text(
        json.dumps(access, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    code, out, err = run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
    log = (out or "") + (err or "")

    if code != 0:
        USERS.write_text(old_users_text, encoding="utf-8")
        access_path.write_text(old_access_text, encoding="utf-8")
        run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
        return False, f"Удаление {slug} отменено: apply упал", log

    try:
        settings = load_json(SETTINGS)
        subdir = Path(settings.get("subscription_dir", ""))
        if subdir.exists():
            for suffix in (".txt", "-443.txt", "-8443.txt"):
                f = subdir / f"{slug}{suffix}"
                if f.exists():
                    f.unlink()
    except Exception as e:
        log += f"\ncleanup warning: {e}"

    return True, f"{target.get('name', slug)} удалён", log



def admin_delete_user_force(slug):
    slug = str(slug or "").strip()

    if not slug:
        return False, "slug пустой", ""

    if "/" in slug or "\\" in slug or "\x00" in slug:
        return False, "Некорректный slug", ""

    access_path = globals().get("ACCESS", BASE / "user_access.json")

    old_users_text = USERS.read_text(encoding="utf-8")
    old_access_text = access_path.read_text(encoding="utf-8") if access_path.exists() else "{}\n"

    try:
        users_data = json.loads(old_users_text)
        users = users_data.get("users", [])
    except Exception as e:
        return False, "Не удалось прочитать users.json", str(e)

    target = None
    kept = []

    for u in users:
        if str(u.get("slug", "")) == slug:
            target = u
        else:
            kept.append(u)

    if not target:
        return False, f"Пользователь {slug} не найден", ""

    try:
        access = json.loads(old_access_text) if old_access_text.strip() else {}
    except Exception:
        access = {}

    access.pop(slug, None)
    access.pop(str(target.get("name", "")), None)

    USERS.write_text(
        json.dumps({"users": kept}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    access_path.write_text(
        json.dumps(access, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    code, out, err = run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
    log = (out or "") + (err or "")

    if code != 0:
        USERS.write_text(old_users_text, encoding="utf-8")
        access_path.write_text(old_access_text, encoding="utf-8")
        run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
        return False, f"Удаление {slug} отменено: apply упал", log

    try:
        settings = load_settings()
        subdir = Path(settings.get("subscription_dir", ""))
        if subdir.exists():
            for suffix in (".txt", "-443.txt", "-8443.txt"):
                f = subdir / f"{slug}{suffix}"
                if f.exists():
                    f.unlink()
    except Exception as e:
        log += f"\ncleanup warning: {e}"

    return True, f"{target.get('name', slug)} удалён", log



def admin_async_apply_later(reason=""):
    """
    Запускает vpn-manager apply после ответа браузеру.
    Нужна задержка, потому что apply может перезапустить Xray и оборвать VPN-туннель.
    """
    log = BASE / "admin_async_apply.log"
    safe_reason = re.sub(r"[^a-zA-Z0-9а-яА-Я_.: -]+", "_", str(reason))[:120]
    cmd = (
        "sleep 5; "
        f"echo '\n===== $(date) {safe_reason} =====' >> {log}; "
        f"vpn-manager apply >> {log} 2>&1"
    )
    subprocess.Popen(
        ["bash", "-lc", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def admin_set_user_enabled_local(slug, enabled):
    slug = str(slug or "").strip()
    val = 1 if enabled else 0
    with get_db() as conn:
        res = conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (val, slug))
        conn.commit()
        if res.rowcount == 0:
            return False, "Not found", ""
    return True, f"{slug}: {'включён' if enabled else 'отключён'}", ""


def admin_delete_user_local_no_apply(slug):
    slug = str(slug or "").strip()
    if not slug:
        return False, "slug пустой", ""

    if "/" in slug or "\\" in slug or "\x00" in slug:
        return False, "Некорректный slug", ""

    old_users_text = USERS.read_text(encoding="utf-8")

    try:
        users_data = json.loads(old_users_text)
        users = users_data.get("users", [])
    except Exception as e:
        return False, "Не удалось прочитать users.json", str(e)

    target = None
    kept = []

    for u in users:
        if str(u.get("slug", "")) == slug:
            target = u
        else:
            kept.append(u)

    if not target:
        return False, f"Пользователь {slug} не найден", ""

    USERS.write_text(
        json.dumps({"users": kept}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    try:
        access = load_access_codes()
        access.pop(slug, None)
        access.pop(str(target.get("name", "")), None)
        save_access_codes(access)
    except Exception:
        pass

    return True, f"{target.get('name', slug)} удалён (файлы подписки будут очищены после «Применить изменения»)", ""


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, data, content_type="text/html; charset=utf-8", status_code=200, extra_headers=None):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)

        self.end_headers()

        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

    def send_html(self, html_text, status_code=200, extra_headers=None):
        self.send_bytes(html_text.encode("utf-8"), "text/html; charset=utf-8", status_code, extra_headers)

    def send_json(self, data, status_code=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(raw, "application/json; charset=utf-8", status_code)

    def redirect(self, location="./", extra_headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

        if extra_headers:
            items = extra_headers.items() if hasattr(extra_headers, "items") else extra_headers
            for k, v in items:
                self.send_header(k, v)

        self.end_headers()

    def form(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        parsed = parse_qs(body)
        return {k: v[0] if v else "" for k, v in parsed.items()}

    def get_cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.strip().split("=", 1)
            if k == name:
                return v
        return ""

    def is_authed(self):
        return verify_token(self.get_cookie(COOKIE_NAME))

    def require_auth(self):
        if self.is_authed():
            return True
        self.send_html(render_login(), 401)
        return False

    def current_session_token(self):
        return self.get_cookie(COOKIE_NAME)

    def current_csrf(self):
        return csrf_token(self.current_session_token())

    def verify_csrf_from_form(self, form_data):
        sent = str(form_data.get("csrf", "")).strip()
        expected = self.current_csrf()
        return bool(sent and expected and hmac.compare_digest(sent, expected))

    def real_client_ip(self):
        x_real_ip = (self.headers.get("X-Real-IP", "") or "").strip()
        if x_real_ip:
            return x_real_ip
        xff = (self.headers.get("X-Forwarded-For", "") or "").strip()
        if xff:
            first = xff.split(",", 1)[0].strip()
            if first:
                return first
        return self.client_address[0] if self.client_address else "unknown"

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        public_get_routes = {"/health", "/logout"}
        if path in public_get_routes:
            if path == "/health":
                self.send_json({"ok": True, "service": "vpn-admin", "version": VERSION})
                return
            self.redirect("./", [
                ("Set-Cookie", f"{COOKIE_NAME}=deleted; Max-Age=0; Path={COOKIE_PATH}; HttpOnly; Secure; SameSite=Lax"),
                ("Set-Cookie", f"{COOKIE_NAME}=deleted; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"),
            ])
            return

        if not self.require_auth():
            return

        if path == "/invite":
            qs = parse_qs(parsed.query)
            slug = qs.get("slug", [""])[0]
            users = load_users()
            settings = load_settings()
            codes = ensure_access_codes(users)

            user = None
            for u in users:
                if str(u.get("slug", "")) == str(slug):
                    user = u
                    break

            if not user:
                self.send_html(render("Пользователь не найден.", csrf=self.current_csrf()), 404)
                return

            name = str(user.get("name", slug))
            access_code = codes.get(slug, "")
            user_page = public_user_invite_url(settings)
            invite_text = (
                f"🔐 VPN доступ — {name}\n\n"
                f"Страница подключения:\n{user_page}\n\n"
                f"Код доступа: {access_code}\n\n"
                "Если ссылка не открылась, откройте её в браузере вручную.\n"
            )
            self.send_html(render_invite_page(user, invite_text, user_page, access_code))
            return

        if path == "/qr":
            qs = parse_qs(parsed.query)
            slug = qs.get("slug", [""])[0]
            kind = qs.get("kind", ["vpn"])[0]

            users = load_users()
            settings = load_settings()
            ensure_access_codes(users)

            user = None
            for u in users:
                if str(u.get("slug", "")) == str(slug):
                    user = u
                    break

            if not user:
                self.send_bytes(b"Bad user", "text/plain; charset=utf-8", 404)
                return

            token = user.get("token", slug)
            text = f"{subscription_base(settings)}/{token}.txt"

            if kind == "invite":
                codes = load_access_codes()
                text = f"Страница подключения: {public_user_invite_url(settings)}\nКод доступа: {codes.get(slug, '')}"

            svg = qr_svg(text)
            self.send_bytes(svg, "image/svg+xml; charset=utf-8")
            return

        if path == "/":
            self.send_html(render(csrf=self.current_csrf()))
            return

        self.redirect("./")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/login":
            f = self.form()
            username = f.get("username", "").strip()
            password = f.get("password", "")
            client_ip = self.real_client_ip()

            if too_many_attempts(client_ip):
                self.send_html(render_login("Слишком много попыток входа. Подождите 10 минут."), 429)
                return

            if check_password(username, password):
                clear_attempts(client_ip)
                token = make_token(username)
                self.redirect("./", {
                    "Set-Cookie": f"{COOKIE_NAME}={token}; Max-Age={COOKIE_MAX_AGE}; Path={COOKIE_PATH}; HttpOnly; Secure; SameSite=Lax"
                })
            else:
                remember_failed_attempt(client_ip)
                self.send_html(render_login("Неверный логин или пароль."), 401)
            return

        if not self.require_auth():
            return

        f = self.form()
        if not self.verify_csrf_from_form(f):
            self.send_html(render("CSRF validation failed. Обновите страницу и попробуйте снова.", csrf=self.current_csrf()), 403)
            return

        if path == "/apply":
            code, out, err = run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"], timeout=180)
            if code == 0:
                clear_pending_changes()
            msg = "Конфиг применён" if code == 0 else "Ошибка применения"
            self.send_html(render(msg, out + err, csrf=self.current_csrf()))
            return

        if path == "/add":
            name = f.get("name", "").strip()
            slug = f.get("slug", "").strip()

            if not name:
                self.send_html(render("Имя не указано", csrf=self.current_csrf()))
                return

            cmd = ["python3", "/root/vpn-manager/app/vpn-manager.py", "add-user", name]
            if slug:
                cmd += ["--slug", slug]

            code1, out1, err1 = run(cmd, timeout=120)
            if code1 != 0:
                self.send_html(render("Ошибка создания пользователя", out1 + err1, csrf=self.current_csrf()))
                return

            actual_slug = slug
            m = re.search(r"^Slug:\s*(\S+)", out1 or "", re.M)
            if m:
                actual_slug = m.group(1).strip()

            users = load_users()
            ensure_access_codes(users)
            mark_pending_change("add", actual_slug or slug or name)

            # Без apply: новый пользователь появится в живом Xray только после кнопки «Применить изменения».
            self.redirect("./?v=add-local-" + str(int(time.time())))
            return

        if path == "/toggle":
            slug = f.get("slug", "").strip()
            action = f.get("action", "").strip()

            if action not in ("enable", "disable"):
                self.send_html(render("Некорректное действие", csrf=self.current_csrf()))
                return

            ok, msg, log = admin_set_user_enabled_local(slug, action == "enable")
            if not ok:
                self.send_html(render(msg, log, csrf=self.current_csrf()))
                return

            mark_pending_change(f"toggle:{action}", slug)
            # ВАЖНО: без vpn-manager apply, чтобы не ронять VPN всем.
            self.redirect("./?v=toggle-local-" + str(int(time.time())))
            return

        if path == "/rotate-code":
            slug = f.get("slug", "").strip()

            if not slug:
                self.send_html(render("Slug не указан", csrf=self.current_csrf()))
                return

            rotate_access_code(slug)
            self.redirect("./?v=code-rotated-" + str(int(time.time())))
            return

        if path == "/delete-user":
            slug = f.get("slug", "").strip()
            ok, msg, log = admin_delete_user_local_no_apply(slug)
            if ok:
                mark_pending_change("delete", slug)
                self.redirect(f"./?v=deleted-{int(time.time())}")
            else:
                self.send_html(render(msg, log, csrf=self.current_csrf()))
            return


        self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)


if __name__ == "__main__":
    ensure_access_codes(load_users())
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"VPN admin listening on http://{HOST}:{PORT}")
    server.serve_forever()
