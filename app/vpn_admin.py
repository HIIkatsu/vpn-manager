#!/usr/bin/env python3
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
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

HOST = "127.0.0.1"
PORT = 8010

COOKIE_NAME = "vpn_admin_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
COOKIE_PATH = "/vpn-admin/"
VERSION = "admin-old-ui-pending-v5-invite-copy-fix"
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
    return load_json(USERS).get("users", [])


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
        if ACCESS.exists():
            return json.loads(ACCESS.read_text())
    except Exception:
        pass
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
    codes = load_access_codes()
    used = set(str(v).upper() for v in codes.values())
    changed = False

    for user in users:
        slug = str(user.get("slug", "")).strip()
        if not slug:
            continue

        current = str(codes.get(slug, "")).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{6,10}", current):
            while True:
                current = make_access_code()
                if current not in used:
                    break
            codes[slug] = current
            used.add(current)
            changed = True
        elif codes.get(slug) != current:
            codes[slug] = current
            changed = True

    if changed:
        save_access_codes(codes)

    return codes


def rotate_access_code(slug):
    codes = load_access_codes()
    used = set(str(v).upper() for k, v in codes.items() if str(k) != str(slug))

    while True:
        code = make_access_code()
        if code not in used:
            break

    codes[str(slug)] = code
    save_access_codes(codes)
    return code


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


def render_pending_box(pending, csrf=""):
    changes = pending.get("changes", [])
    if not changes:
        return ""

    labels = {
        "delete": "Удалён профиль",
        "toggle:enable": "Включён профиль",
        "toggle:disable": "Отключён профиль",
        "add": "Добавлен профиль",
        "rotate-code": "Обновлён код доступа",
    }

    rows = ""
    for item in reversed(changes[-20:]):
        action = str(item.get("action", "change"))
        slug = str(item.get("slug", "")) or "—"
        ts = int(item.get("ts", 0) or 0)
        when = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        rows += f'<li><span>{esc(labels.get(action, action))}</span><b>{esc(slug)}</b><small>{esc(when)}</small></li>'

    return f"""
    <section class="pending-compact">
      <button type="button" class="pending-trigger" onclick="openPendingModal()">Есть несохранённые изменения <span>{len(changes)}</span></button>
    </section>

    <div class="modal" id="pendingModal" onclick="if(event.target===this)closePendingModal()">
      <div class="modal-card">
        <div class="modal-head">
          <div>
            <b>Несохранённые изменения</b>
            <div class="muted">Изменения в админке уже сохранены, но в Xray применятся только после подтверждения.</div>
          </div>
          <button type="button" onclick="closePendingModal()">✕</button>
        </div>
        <ul class="pending-modal-list">{rows}</ul>
        <form method="post" action="apply" class="pending-apply-form">
          <input type="hidden" name="csrf" value="{esc(csrf)}">
          <button class="primary" type="submit">Применить изменения</button>
        </form>
      </div>
    </div>
    """


def public_user_path(settings):
    sub = settings.get("subscription_path", "vpn")
    if sub.startswith("vpn-"):
        return "vpn-user-" + sub.split("vpn-", 1)[1]
    return sub + "-user"


def public_user_url(settings):
    return f"https://{settings['domain']}/{public_user_path(settings)}/"


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
    return f'<span class="badge {cls}">{esc(text)}</span>'


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
:root{color-scheme:dark;--bg:#080d17;--panel:#111827;--card:#151d2b;--line:rgba(255,255,255,.10);--text:#edf2ff;--muted:#94a2bb;--accent:#6ea8ff;--ok:#54dba6;--bad:#ff6f82;--radius:20px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at 20% 0,rgba(110,168,255,.18),transparent 30rem),var(--bg);color:var(--text)}
.wrap{max-width:1100px;margin:0 auto;padding:14px 12px 30px}
.hero{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:12px}
h1{margin:0;font-size:clamp(30px,7vw,44px);letter-spacing:-.04em}
.subtitle{color:var(--muted);font-size:14px;margin-top:6px}
.logout{padding:8px 10px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);text-decoration:none;font-weight:700}
.server-dock,.panel,.user-card,.modal-card{background:linear-gradient(160deg,rgba(255,255,255,.06),rgba(255,255,255,.03));border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 10px 30px rgba(0,0,0,.25)}
.server-dock,.panel{padding:14px;margin-bottom:12px}
.dock-title{font-size:22px;font-weight:900}.dock-sub{color:var(--muted);font-size:13px;margin-top:4px}
.service-line{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-top:10px}.service-pill{padding:10px;border:1px solid var(--line);border-radius:14px;background:rgba(0,0,0,.15)}
.badge{padding:5px 10px;border-radius:999px;font-size:12px;font-weight:800}.badge.ok{background:rgba(84,219,166,.14);color:#b8ffe3}.badge.bad{background:rgba(255,111,130,.15);color:#ffdbe1}
.pending-compact{margin:0 0 12px}.pending-trigger{width:100%;min-height:44px;border-radius:14px;border:1px solid rgba(255,201,102,.36);background:rgba(255,201,102,.12);color:#ffefc5;font-weight:800;display:flex;justify-content:space-between;align-items:center;padding:0 12px}
.pending-trigger span{background:rgba(0,0,0,.2);padding:2px 8px;border-radius:999px}
.form-row,.toolbar{display:grid;gap:8px}.form-row{grid-template-columns:1fr 1fr auto}.toolbar{grid-template-columns:1fr 160px}
input,select,button,.button-link{min-height:44px;padding:0 12px;border-radius:13px;border:1px solid var(--line);background:rgba(0,0,0,.23);color:var(--text);font-size:14px}
button,.button-link{text-decoration:none;display:inline-flex;align-items:center;justify-content:center;font-weight:800;cursor:pointer}
button.primary,.button-link.primary{background:linear-gradient(130deg,rgba(110,168,255,.35),rgba(110,168,255,.17));border-color:rgba(110,168,255,.4)}
button.danger,.delete-btn{background:rgba(255,111,130,.12)!important;border-color:rgba(255,111,130,.34)!important;color:#ffdce4!important}
.users-grid{display:grid;grid-template-columns:1fr;gap:10px}
.user-card{padding:14px}.user-top-v5{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.user-title{font-size:20px;font-weight:900}.muted{color:var(--muted);font-size:13px}
.user-main-v5{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.code-card-v5,.traffic-card-v5,.user-share-v5{border:1px solid var(--line);border-radius:14px;padding:10px;background:rgba(0,0,0,.16)}
.access-code{font-size:26px;font-weight:900;letter-spacing:.08em}.share-actions-v5,.admin-actions-v5{display:grid;gap:8px}.share-actions-v5{grid-template-columns:1fr 1fr}.admin-actions-v5{grid-template-columns:repeat(4,1fr);margin-top:10px}
.details{margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:14px;background:rgba(0,0,0,.15)}
.kv{display:grid;grid-template-columns:80px 1fr;gap:6px;margin-top:8px}code{font-size:12px;word-break:break-all}
.msg{margin-bottom:10px;padding:10px;border:1px solid rgba(84,219,166,.3);background:rgba(84,219,166,.12);border-radius:12px}
.log-details{margin-bottom:10px}.log-details pre{max-height:260px;overflow:auto;padding:10px;border-radius:12px;border:1px solid var(--line);background:rgba(0,0,0,.2)}
.modal{display:none;position:fixed;inset:0;padding:12px;background:rgba(0,0,0,.65);align-items:flex-end;justify-content:center;z-index:30}.modal.show{display:flex}.modal-card{width:min(560px,100%);padding:14px;border-radius:18px}.modal-head{display:flex;justify-content:space-between;gap:8px;margin-bottom:10px}.pending-modal-list{list-style:none;padding:0;margin:0 0 12px;max-height:280px;overflow:auto}.pending-modal-list li{display:grid;grid-template-columns:1fr auto auto;gap:8px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.pending-apply-form button{width:100%}
.toast{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);background:#0f1725;border:1px solid var(--line);padding:10px 12px;border-radius:12px;opacity:0;transition:.2s}.toast.show{opacity:1}
@media(min-width:900px){.users-grid{grid-template-columns:1fr 1fr}.modal{align-items:center}.wrap{padding-top:20px}.hero{margin-bottom:16px}}
@media(max-width:760px){.form-row,.toolbar,.user-main-v5,.share-actions-v5,.admin-actions-v5{grid-template-columns:1fr 1fr}.form-row input:first-of-type{grid-column:1/-1}.toolbar input{grid-column:1/-1}}
</style>
"""


JS = """
<script>
function showToast(text){const el=document.getElementById('toast');if(!el)return;el.textContent=text;el.classList.add('show');clearTimeout(window.__t);window.__t=setTimeout(()=>el.classList.remove('show'),1800)}
function filterUsers(){const q=(document.getElementById('search')?.value||'').toLowerCase().trim();const st=document.getElementById('stateFilter')?.value||'all';document.querySelectorAll('.user-card').forEach(c=>{const okQ=!q||c.dataset.name.includes(q)||c.dataset.slug.includes(q);const okS=st==='all'||c.dataset.state===st;c.classList.toggle('hidden',!(okQ&&okS));});}
async function copyText(text,id){if(id){const el=document.getElementById(id);if(el){el.focus();el.select();}}try{await navigator.clipboard.writeText(text||'');showToast('Скопировано');return}catch(e){};try{const t=document.createElement('textarea');t.value=text||'';document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();showToast('Скопировано');}catch(e){showToast('Скопируйте вручную')}}
function openPendingModal(){document.getElementById('pendingModal')?.classList.add('show')}
function closePendingModal(){document.getElementById('pendingModal')?.classList.remove('show')}
window.showQr=function(slug,name,kind){const m=document.getElementById('qrModal');const i=document.getElementById('qrImg');if(!m||!i)return false;document.getElementById('qrTitle').textContent='QR · '+(name||slug);i.src='qr?slug='+encodeURIComponent(slug||'')+'&kind='+(kind||'vpn')+'&t='+Date.now();m.classList.add('show');return false;}
window.closeQr=function(){document.getElementById('qrModal')?.classList.remove('show')}
window.hideQr=function(e){if(!e||e.target.id==='qrModal')window.closeQr()}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closePendingModal();window.closeQr();}})
document.addEventListener('click',function(e){const b=e.target.closest('.qr-admin-btn');if(b){e.preventDefault();window.showQr(b.dataset.qrSlug,b.dataset.qrName,'vpn')}})
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
      <a class="button-link" href="{esc(user_page_url)}" target="_blank" rel="noopener">Открыть страницу</a>
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
    pending_html = render_pending_box(pending, csrf=csrf)

    base_url = subscription_base(settings)
    user_page_url = public_user_url(settings)

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

        encoded_slug = quote(slug, safe="")
        link = f"{base_url}/{encoded_slug}.txt"
        fallback_link = f"{base_url}/{encoded_slug}-8443.txt"

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
            f"🔐 VPN доступ — {name}\\n\\n"
            f"Страница подключения:\\n{user_page_url}\\n\\n"
            f"Код доступа: {access_code}\\n"
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
                    <a class="button-link" href="invite?slug={esc(encoded_slug)}" target="_blank" rel="noopener">Инвайт</a>
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
                <a class="button-link qr-admin-btn" href="qr?slug={esc(encoded_slug)}&kind=vpn" target="_blank" rel="noopener" data-qr-slug="{esc(slug)}" data-qr-name="{esc(name)}">QR</a>
                
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
  <header class="hero">
    <div class="hero-inner">
      <div>
        <h1>🇳🇱 VPN Admin</h1>
        <div class="subtitle">Пользователи, коды доступа, подписки и состояние сервера</div>
      </div>
      <a class="logout" href="logout" aria-label="Выйти">Выйти</a>
    </div>
  </header>

  {pending_html}
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

    code, out, err = run(["vpn-manager", "apply"], timeout=180)

    if code != 0:
        USERS.write_text(old_users_text)
        try:
            if old_access_text:
                ACCESS.write_text(old_access_text)
        except Exception:
            pass

        rollback_code, rollback_out, rollback_err = run(["vpn-manager", "apply"], timeout=180)
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

    code, out, err = run(["vpn-manager", "apply"], timeout=180)
    log = (out or "") + (err or "")

    if code != 0:
        USERS.write_text(old_users_text, encoding="utf-8")
        access_path.write_text(old_access_text, encoding="utf-8")
        run(["vpn-manager", "apply"], timeout=180)
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

    code, out, err = run(["vpn-manager", "apply"], timeout=180)
    log = (out or "") + (err or "")

    if code != 0:
        USERS.write_text(old_users_text, encoding="utf-8")
        access_path.write_text(old_access_text, encoding="utf-8")
        run(["vpn-manager", "apply"], timeout=180)
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
        f"echo '\\n===== $(date) {safe_reason} =====' >> {log}; "
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
    if not slug:
        return False, "slug пустой", ""

    old_text = USERS.read_text(encoding="utf-8")

    try:
        data = json.loads(old_text)
        users = data.get("users", [])
    except Exception as e:
        return False, "Не удалось прочитать users.json", str(e)

    found = False
    for u in users:
        if str(u.get("slug", "")) == slug:
            u["enabled"] = bool(enabled)
            found = True
            break

    if not found:
        return False, f"Пользователь {slug} не найден", ""

    USERS.write_text(
        json.dumps({"users": users}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

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

    try:
        settings = load_settings()
        subdir = Path(settings.get("subscription_dir", ""))
        encoded_slug = quote(slug, safe="")

        if subdir.exists():
            for filename in (
                f"{slug}.txt",
                f"{slug}-443.txt",
                f"{slug}-8443.txt",
                f"{encoded_slug}.txt",
                f"{encoded_slug}-443.txt",
                f"{encoded_slug}-8443.txt",
            ):
                f = subdir / filename
                if f.exists():
                    f.unlink()
    except Exception as e:
        return True, f"{target.get('name', slug)} удалён, но файлы подписки частично не очищены: {e}", ""

    return True, f"{target.get('name', slug)} удалён", ""


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
            user_page = public_user_url(settings)
            invite_text = (
                f"🔐 VPN доступ — {name}\n\n"
                f"Страница подключения:\n{user_page}\n\n"
                f"Код доступа: {access_code}\n"
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

            encoded_slug = quote(str(slug), safe="")
            text = f"{subscription_base(settings)}/{encoded_slug}.txt"

            if kind == "invite":
                codes = load_access_codes()
                text = f"Страница подключения: {public_user_url(settings)}\nКод доступа: {codes.get(slug, '')}"

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
            code, out, err = run(["vpn-manager", "apply"], timeout=180)
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

            cmd = ["vpn-manager", "add-user", name]
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
