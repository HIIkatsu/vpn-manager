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
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

BASE = Path("/root/vpn-manager")
USERS = BASE / "users.json"
SETTINGS = BASE / "settings.json"
AUTH = BASE / "auth.json"
ACCESS = BASE / "user_access.json"
SECRET_FILE = BASE / ".vpn_user_secret"

HOST = "127.0.0.1"
PORT = 8011

COOKIE_NAME = "vpn_user_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
VERSION = "vpn-user-ui-premium-compact-v6"

TRAFFIC_SOFT_LIMIT = 1024 * 1024 * 1024  # 1 GB soft bar scale if user is alone

def resolve_icon_dir():
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "assets" / "icons",
        script_dir / "app" / "assets" / "icons",
        BASE / "app" / "assets" / "icons",
        BASE / "assets" / "icons",
    ]
    for icon_dir in candidates:
        if (icon_dir / "icon_manifest.json").exists():
            return icon_dir
    return candidates[0]


ICON_DIR = resolve_icon_dir()
ICON_MANIFEST = ICON_DIR / "icon_manifest.json"


def load_local_icon(name: str):
    try:
        file_path = (ICON_DIR / name).resolve()
        if ICON_DIR.resolve() not in file_path.parents:
            return ""
    except Exception:
        return ""
    ext = file_path.suffix.lower()
    if ext == ".svg":
        try:
            raw = file_path.read_text(encoding="utf-8").strip()
            return re.sub(r"<\?xml[^>]*>\s*", "", raw, flags=re.I)
        except Exception:
            return ""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext)
    if not mime:
        return ""
    try:
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    except Exception:
        return ""
    return f"data:{mime};base64,{encoded}"


def load_icon_set():
    defaults = {
        "hiddify": "hiddify.svg",
        "happ": "happ.png",
        "v2rayng": "v2rayng.png",
        "nekobox": "nekobox.png",
        "streisand": "streisand.png",
    }
    try:
        manifest = load_json(ICON_MANIFEST, default={})
    except Exception:
        manifest = {}
    icons = {}
    for key, fallback in defaults.items():
        icon_name = str(manifest.get(key) or fallback)
        icon_data = load_local_icon(icon_name)
        if icon_data:
            icons[key] = icon_data
    return icons


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def save_json(path: Path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def run(cmd, timeout=30, input_text=None):
    p = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
        input=input_text,
    )
    return p.returncode, p.stdout, p.stderr


def shell(cmd, timeout=30):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, shell=True)
    return p.returncode, p.stdout, p.stderr


def esc(value):
    return html.escape(str(value), quote=True)


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


def mask_link(url: str):
    url = str(url or "")
    if len(url) <= 38:
        return url
    return url[:28] + "••••••••" + url[-10:]


def get_secret_bytes():
    if AUTH.exists():
        try:
            data = load_json(AUTH)
            raw = data.get("secret", "")
            if raw:
                return base64.b64decode(raw)
        except Exception:
            pass

    if SECRET_FILE.exists():
        raw = SECRET_FILE.read_bytes().strip()
        if raw:
            return raw

    secret = os.urandom(32)
    SECRET_FILE.write_bytes(secret)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return secret


def sign(payload: str):
    secret = get_secret_bytes()
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_token(slug: str):
    exp = int(time.time()) + COOKIE_MAX_AGE
    payload = base64.urlsafe_b64encode(f"{slug}:{exp}".encode()).decode().rstrip("=")
    return payload + "." + sign(payload)


def verify_token(token: str):
    if not token or "." not in token:
        return ""
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sign(payload), sig):
        return ""
    try:
        padded = payload + "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        slug, exp_s = raw.rsplit(":", 1)
        if int(exp_s) < int(time.time()):
            return ""
        return slug
    except Exception:
        return ""


def load_users():
    data = load_json(USERS, {"users": []})
    return data.get("users", [])


def load_settings():
    return load_json(SETTINGS)


def make_access_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(7))
        if not any(bad in code for bad in ("BAD", "XXX", "SEX", "FUK")):
            return code


def load_access_codes():
    data = load_json(ACCESS, {})
    return data if isinstance(data, dict) else {}


def save_access_codes(codes):
    save_json(ACCESS, codes, mode=0o600)


def ensure_access_codes(users=None):
    users = users if users is not None else load_users()
    codes = load_access_codes()
    used = {str(v).upper() for v in codes.values()}
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

    valid_slugs = {str(u.get("slug", "")) for u in users}
    for key in list(codes.keys()):
        if key not in valid_slugs:
            codes.pop(key, None)
            changed = True

    if changed:
        save_access_codes(codes)
    return codes


def slug_by_code(code, users=None):
    code = str(code or "").strip().upper().replace(" ", "")
    if not code:
        return ""
    users = users if users is not None else load_users()
    codes = ensure_access_codes(users)
    for slug, saved in codes.items():
        if str(saved).strip().upper() == code:
            return slug

    # Backward compatibility with old embedded codes in users.json.
    for user in users:
        for key in ("access_code", "public_code", "code", "login_code"):
            if str(user.get(key, "")).strip().upper() == code:
                return str(user.get("slug", ""))
    return ""


def public_user_path(settings):
    sub = str(settings.get("subscription_path", "vpn")).strip("/")
    if sub.startswith("vpn-"):
        return "vpn-user-" + sub.split("vpn-", 1)[1]
    return sub + "-user"


def cookie_path():
    try:
        return "/" + public_user_path(load_settings()).strip("/") + "/"
    except Exception:
        return "/"


def record_invite_event(slug: str, event: str, meta=None):
    try:
        data = load_json(INVITE_EVENTS, {"events": []})
        if not isinstance(data, dict):
            data = {"events": []}
        if not isinstance(data.get("events"), list):
            data["events"] = []
        rec = {"slug": str(slug), "event": str(event), "ts": int(time.time())}
        if isinstance(meta, dict) and meta:
            rec["meta"] = meta
        data["events"].append(rec)
        data["events"] = data["events"][-5000:]
        save_json(INVITE_EVENTS, data)
    except Exception:
        pass

def find_user(slug: str):
    for user in load_users():
        if str(user.get("slug", "")) == str(slug):
            return user
    return None


def find_user_by_code(code: str):
    code = str(code or "").strip()
    if not code:
        return None

    users = load_users()
    ensure_access_codes(users)

    slug = slug_by_code(code, users)
    if not slug:
        return None

    user = find_user(slug)
    if not user or not user.get("enabled", True):
        return None

    return user

def subscription_base(settings: dict):
    return f"https://{settings['domain']}/{str(settings['subscription_path']).strip('/')}"


def subscription_dir(settings: dict):
    # Critical fix: files live in settings["subscription_dir"], not always /var/www/<subscription_path>.
    return Path(settings.get("subscription_dir") or (Path("/var/www") / str(settings["subscription_path"]).strip("/")))


def client_host(settings: dict):
    return settings.get("client_host") or settings.get("server_ip") or settings["domain"]


def vless_link(settings: dict, user: dict, port=None, title=None):
    port = int(port or settings.get("public_port", 443))
    profile_title = title or user.get("title") or settings.get("profile_title") or f"VPN {user['slug']}"
    query = {
        "type": "tcp",
        "encryption": "none",
        "security": "reality",
        "sni": settings["sni"],
        "fp": settings.get("fingerprint", "chrome"),
        "pbk": settings["public_key"],
        "sid": settings["short_id"],
        "flow": settings.get("flow", "xtls-rprx-vision"),
    }
    qs = "&".join(f"{k}={quote(str(v), safe='-_~.')}" for k, v in query.items() if v)
    return f"vless://{user['uuid']}@{client_host(settings)}:{port}?{qs}#{quote(profile_title)}"


def raw_vless_from_file(settings: dict, user: dict):
    path = subscription_dir(settings) / f"{user['slug']}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("vless://"):
            return text
    return vless_link(settings, user, int(settings.get("public_port", 443)))


def fallback_vless(settings: dict, user: dict):
    port = int(settings.get("fallback_port", settings.get("xray_port", 8443)))
    path = subscription_dir(settings) / f"{user['slug']}-{port}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("vless://"):
            return text
    return vless_link(settings, user, port, f"{settings.get('profile_title', 'VPN')} fallback {port}")


def server_status():
    x_code, x_out, _ = run(["systemctl", "is-active", "xray"], timeout=15)
    n_code, n_out, _ = run(["systemctl", "is-active", "nginx"], timeout=15)
    p443_code, _, _ = shell("ss -lnt | grep -q ':443 '", timeout=15)

    ok = (
        x_code == 0
        and x_out.strip() == "active"
        and n_code == 0
        and n_out.strip() == "active"
        and p443_code == 0
    )

    if ok:
        return {"text": "Сервер работает", "badge": "Доступен", "class": "ok"}
    return {"text": "Есть проблема", "badge": "CHECK", "class": "bad"}


def parse_xray_stats(raw: str):
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

    return result


def xray_stats():
    commands = [
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", "user>>>"],
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085", "-pattern", "user"],
        ["xray", "api", "statsquery", "--server=127.0.0.1:10085"],
    ]
    api_reachable = False
    for cmd in commands:
        code, out, err = run(cmd, timeout=15)
        raw = (out + "\n" + err).strip()
        if code == 0:
            api_reachable = True
            parsed = parse_xray_stats(raw)
            if parsed:
                return parsed, True
    return {}, api_reachable


def journal_activity(minutes=30):
    cmd = ["journalctl", "-u", "xray", f"--since={minutes} minutes ago", "--no-pager", "-o", "short-iso"]
    code, out, err = run(cmd, timeout=20)
    raw = out + "\n" + err
    data = {}
    if code != 0:
        return data

    for line in raw.splitlines():
        m = re.search(r"email:\s*([A-Za-z0-9_\-]+)", line)
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


def all_user_stats():
    users = load_users()
    stats, stats_ok = xray_stats()
    activity = journal_activity(30)

    totals = {}
    max_total = 0
    for user in users:
        slug = str(user.get("slug", ""))
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
        max_total = max(max_total, total)

    if max_total <= 0:
        max_total = TRAFFIC_SOFT_LIMIT

    return totals, max_total, stats_ok


def progress_percent(total: int, max_total: int):
    if total <= 0:
        return 6
    pct = int((total / max_total) * 100) if max_total > 0 else 0
    if pct < 12:
        pct = 12
    if pct > 100:
        pct = 100
    return pct


def qr_svg(text: str):
    code, out, err = run(["qrencode", "-t", "SVG", "-o", "-", text], timeout=20)
    if code == 0 and out.strip():
        return out.encode("utf-8")
    fallback = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">
  <rect width="100%" height="100%" fill="white"/>
  <text x="20" y="120" font-size="16" fill="black">QR error</text>
</svg>"""
    return fallback.encode("utf-8")


STYLE = """
<style>
:root{
  color-scheme:dark;
  --bg:#050a14;
  --bg-soft:#0b1220;
  --card:rgba(255,255,255,.08);
  --card2:rgba(255,255,255,.05);
  --line:rgba(255,255,255,.10);
  --text:#eef3ff;
  --muted:#a4b0c9;
  --accent:#5f8fff;
  --accent2:#42e3c8;
  --ok:#54e0aa;
  --bad:#ff7382;
  --shadow:0 22px 70px rgba(0,0,0,.30);
}
*{box-sizing:border-box}
html{
  scroll-behavior:smooth;
  min-height:100%;
  background:var(--bg);
}
body{
  margin:0;
  min-height:100vh;
  font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  color:var(--text);
  background:
    radial-gradient(circle at 18% 0%, rgba(88,132,255,.23), transparent 26rem),
    radial-gradient(circle at 92% 8%, rgba(66,227,200,.12), transparent 28rem),
    radial-gradient(circle at 50% 100%, rgba(255,255,255,.05), transparent 32rem),
    var(--bg);
}
.wrap{
  max-width:900px;
  margin:0 auto;
  padding:16px 14px 32px;
}
.wrap.login-wrap{
  min-height:100vh;
  display:flex;
  flex-direction:column;
  justify-content:center;
}
.hero{
  margin-bottom:12px;
}
.hero h1{
  margin:0;
  font-size:clamp(32px,8vw,54px);
  letter-spacing:-.07em;
  line-height:.98;
}
.subtitle{
  margin-top:7px;
  color:var(--muted);
  font-size:15px;
}
.card{
  background:linear-gradient(145deg, rgba(255,255,255,.09), rgba(255,255,255,.045));
  border:1px solid var(--line);
  border-radius:28px;
  padding:16px;
  box-shadow:var(--shadow);
  backdrop-filter:blur(20px);
  -webkit-backdrop-filter:blur(20px);
  margin-bottom:14px;
}
.hero-card{padding:18px}
.hero-card-head{
  display:flex;justify-content:space-between;align-items:flex-start;gap:10px;
}
.hero-title{font-size:26px;font-weight:940;letter-spacing:-.03em;line-height:1.05}
.hero-sub{margin-top:8px;color:var(--muted);font-size:14px;max-width:48ch}
.hero-helper{
  margin-top:12px;
  padding:12px 14px;
  border-radius:16px;
  font-size:14px;
  border:1px solid rgba(255,255,255,.08);
  background:rgba(0,0,0,.14);
}
.status-card,.server-card{display:flex;justify-content:space-between;align-items:center;gap:14px}
.server-left{
  display:flex;
  align-items:center;
  gap:14px;
  min-width:0;
}
.flag{
  width:74px;
  height:74px;
  border-radius:22px;
  display:grid;
  place-items:center;
  font-size:42px;
  background:rgba(255,255,255,.07);
  border:1px solid var(--line);
  flex:0 0 auto;
}
.server-name{
  font-size:28px;
  line-height:1.05;
  font-weight:950;
  letter-spacing:-.04em;
}
.location{
  margin-top:6px;
  color:var(--muted);
  font-size:15px;
}
.badge{
  padding:10px 16px;
  border-radius:999px;
  font-size:13px;
  font-weight:900;
  white-space:nowrap;
}
.badge.ok{
  color:var(--ok);
  background:rgba(84,224,170,.13);
  border:1px solid rgba(84,224,170,.18);
}
.badge.bad{
  color:#ffd9df;
  background:rgba(255,115,130,.14);
  border:1px solid rgba(255,115,130,.2);
}
.traffic-head{
  display:flex;
  justify-content:space-between;
  align-items:flex-end;
  gap:12px;
}
.label{
  color:var(--muted);
  font-size:13px;
  text-transform:uppercase;
  letter-spacing:.08em;
}
.traffic-title{
  margin-top:4px;
  font-size:22px;
  font-weight:900;
  letter-spacing:-.03em;
}
.traffic-total{
  font-size:30px;
  font-weight:950;
  letter-spacing:-.05em;
  color:var(--accent2);
}
.progress{
  margin:16px 0 12px;
  height:16px;
  border-radius:999px;
  background:rgba(255,255,255,.09);
  overflow:hidden;
  border:1px solid rgba(255,255,255,.05);
}
.progress i{
  display:block;
  height:100%;
  border-radius:999px;
  background:linear-gradient(90deg, #5f8fff 0%, #4ab5ff 30%, #42e3c8 100%);
  box-shadow:0 0 28px rgba(66,227,200,.25);
}
.traffic-meta{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:10px;
}
.metric{
  padding:12px;
  border-radius:18px;
  background:rgba(0,0,0,.18);
  border:1px solid rgba(255,255,255,.06);
}
.metric .k{
  color:var(--muted);
  font-size:12px;
  margin-bottom:4px;
}
.metric .v{
  font-size:15px;
  font-weight:850;
}
.quick-card{
  display:grid;grid-template-columns:auto 1fr auto;
  gap:14px;
  align-items:center;
}
.qr-tile{
  width:104px;
  height:104px;
  padding:8px;
  border-radius:20px;
  background:rgba(255,255,255,.07);
  border:1px solid rgba(66,227,200,.25);
  display:grid;
  place-items:center;
  box-shadow:inset 0 0 0 1px rgba(66,227,200,.10);
}
.qr-tile img{
  width:100%;
  height:100%;
  object-fit:contain;
  border-radius:12px;
  background:#fff;
}
.quick-title{
  font-size:20px;
  font-weight:900;
  margin-bottom:4px;
}
.muted{
  color:var(--muted);
}
.tiny-note{
  margin-top:10px;
  color:var(--muted);
  font-size:13px;
}
.icon-btn{
  border:none;
  cursor:pointer;
  width:72px;
  height:72px;
  border-radius:22px;
  display:grid;
  place-items:center;
  text-decoration:none;
  color:var(--text);
  font-size:34px;
  border:1px solid var(--line);
  background:linear-gradient(145deg, rgba(255,255,255,.07), rgba(255,255,255,.04));
}
.main-cta-wrap{margin-top:0;margin-bottom:10px}
.section-title{
  color:rgba(255,255,255,.45);
  font-size:12px;
  letter-spacing:.10em;
  text-transform:uppercase;
  margin-bottom:12px;
  font-weight:800;
}
.link-list{
  display:grid;
  gap:10px;
}
.link-row{
  display:grid;
  grid-template-columns:auto 1fr auto;
  gap:12px;
  align-items:center;
  padding:14px;
  border-radius:22px;
  background:rgba(0,0,0,.16);
  border:1px solid rgba(255,255,255,.06);
}
.link-icon{
  width:54px;
  height:54px;
  border-radius:18px;
  display:grid;
  place-items:center;
  background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.06);
  font-size:24px;
}
.link-title{
  font-size:18px;
  font-weight:900;
  letter-spacing:-.02em;
}
.link-preview{
  margin-top:4px;
  color:var(--muted);
  font-size:14px;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
  max-width:100%;
}
.btn,.copy-btn{
  border:none;
  outline:none;
  text-decoration:none;
  color:var(--text);
  background:rgba(255,255,255,.08);
  border:1px solid rgba(255,255,255,.10);
  border-radius:16px;
  min-height:48px;
  padding:0 18px;
  font-size:16px;
  font-weight:900;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
  white-space:nowrap;
}
.copy-btn.primary{
  background:linear-gradient(135deg, rgba(95,143,255,.30), rgba(95,143,255,.18));
  border-color:rgba(95,143,255,.34);
}
.copy-btn.reserve{
  background:linear-gradient(135deg, rgba(66,227,200,.22), rgba(66,227,200,.12));
  border-color:rgba(66,227,200,.22);
}
.actions{
  display:grid;
  gap:10px;
  margin-top:12px;
}
.big-btn{
  width:100%;
  cursor:pointer;
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;
  min-height:58px;
  border-radius:18px;
  text-decoration:none;
  color:var(--text);
  border:1px solid var(--line);
  font-size:18px;
  font-weight:950;
  letter-spacing:-.02em;
}
.big-btn.primary{
  background:linear-gradient(90deg, #5f8fff 0%, #42e3c8 100%);
  color:#fff;
  box-shadow:0 0 34px rgba(66,227,200,.16);
}
.big-btn.secondary{
  background:rgba(255,255,255,.05);
}
details{
  margin-top:12px;
  border-radius:20px;
  border:1px solid rgba(255,255,255,.07);
  background:rgba(0,0,0,.12);
  overflow:hidden;
}
summary{
  list-style:none;
  cursor:pointer;
  padding:16px;
  font-size:17px;
  font-weight:900;
}
summary::-webkit-details-marker{
  display:none;
}
.details-inner{
  padding:0 16px 16px;
  color:var(--muted);
}
.details-hint{font-size:13px;color:var(--muted);margin-top:6px}
.mini-list{margin:10px 0 0;padding-left:18px;color:var(--muted)}
.mini-list li{margin:4px 0}
.steps{
  display:grid;gap:10px;
}
.step{
  padding:14px;
  border-radius:18px;
  border:1px solid rgba(255,255,255,.06);
  background:rgba(0,0,0,.14);
}
.step-num{
  width:36px;
  height:36px;
  border-radius:999px;
  display:grid;
  place-items:center;
  margin-bottom:10px;
  font-weight:900;
  background:rgba(95,143,255,.18);
  color:#7eb0ff;
}
.step b{
  display:block;
  margin-bottom:6px;
  font-size:17px;
}
.step-line{
  display:flex;gap:10px;align-items:flex-start;
}
.step-line .step-num{margin:0;flex:0 0 auto}
.step-line .step-text{padding-top:4px}
.footer-note{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;
  min-height:56px;
  color:var(--text);
  font-weight:800;
}
.auth-card{
  padding:18px;
}
.auth-title{
  margin:0 0 6px;
  font-size:clamp(34px,8vw,54px);
  letter-spacing:-.06em;
  line-height:.95;
}
.auth-sub{
  color:var(--muted);
  margin-bottom:16px;
}
.auth-helper{
  margin-top:10px;
  font-size:13px;
  color:var(--muted);
}
.input{
  width:100%;
  min-height:56px;
  border-radius:18px;
  border:1px solid var(--line);
  background:rgba(0,0,0,.22);
  color:var(--text);
  font-size:18px;
  padding:0 16px;
  outline:none;
}
.input:focus{
  border-color:rgba(95,143,255,.5);
}
.auth-actions{
  display:grid;
  gap:10px;
  margin-top:14px;
}
.error{
  margin-bottom:12px;
  padding:12px 14px;
  border-radius:16px;
  color:#ffd9df;
  background:rgba(255,115,130,.14);
  border:1px solid rgba(255,115,130,.22);
}
.toast{
  position:fixed;
  left:50%;
  bottom:20px;
  transform:translateX(-50%) translateY(20px);
  opacity:0;
  pointer-events:none;
  background:rgba(10,16,27,.92);
  color:#fff;
  border:1px solid rgba(255,255,255,.10);
  border-radius:16px;
  padding:12px 16px;
  font-weight:800;
  transition:.2s ease;
  z-index:99;
}
.toast.show{
  transform:translateX(-50%) translateY(0);
  opacity:1;
}
@media (max-width:720px){
  .wrap.login-wrap{
    justify-content:flex-start;
    padding-top:22px;
  }
  .traffic-meta{
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .quick-card{
    grid-template-columns:84px minmax(0,1fr) 56px;
  }
  .icon-btn{
    width:56px;
    height:56px;
    border-radius:18px;
    font-size:28px;
  }
  .link-row{
    grid-template-columns:1fr;
  }
  .steps{
    grid-template-columns:1fr;
  }
  .server-name{
    font-size:24px;
  }
  .flag{
    width:62px;
    height:62px;
    font-size:36px;
  }
}

.link-row.compact-link{
  grid-template-columns:auto 1fr;
}
.qr-modal{
  position:fixed;
  inset:0;
  z-index:120;
  display:none;
  align-items:center;
  justify-content:center;
  padding:22px;
  background:rgba(2,6,14,.72);
  backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px);
}
.qr-modal.open{
  display:flex;
}
.qr-modal-card{
  position:relative;
  width:min(420px,100%);
  border-radius:32px;
  padding:22px;
  background:
    radial-gradient(circle at 20% 0%, rgba(95,143,255,.20), transparent 18rem),
    linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.055));
  border:1px solid rgba(255,255,255,.14);
  box-shadow:0 28px 90px rgba(0,0,0,.46);
}
.qr-modal-close{
  position:absolute;
  right:16px;
  top:16px;
  width:42px;
  height:42px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.08);
  color:var(--text);
  font-size:24px;
  cursor:pointer;
}
.qr-modal-title{
  padding-right:48px;
  font-size:28px;
  font-weight:950;
  letter-spacing:-.04em;
}
.qr-modal-sub{
  margin-top:6px;
  color:var(--muted);
  font-size:15px;
}
.qr-modal-box{
  margin:20px auto 18px;
  width:min(300px,88vw);
  aspect-ratio:1/1;
  padding:16px;
  border-radius:28px;
  background:#fff;
  box-shadow:0 0 42px rgba(66,227,200,.16);
}
.qr-modal-box img{
  display:block;
  width:100%;
  height:100%;
  object-fit:contain;
}


/* Final mobile polish: no blue tap, fixed QR modal, compact link */
*{
  -webkit-tap-highlight-color: transparent !important;
}
button,
a,
summary,
input{
  -webkit-tap-highlight-color: transparent !important;
  -webkit-touch-callout: none;
}
button{
  appearance:none;
  -webkit-appearance:none;
}
button:focus,
button:active,
a:focus,
a:active,
summary:focus,
summary:active{
  outline:none !important;
  box-shadow:none;
}
.icon-btn{
  border-radius:18px !important;
  overflow:hidden;
  user-select:none;
  -webkit-user-select:none;
  background-clip:padding-box;
}
.icon-btn:active,
.big-btn:active,
.copy-btn:active{
  transform:scale(.97);
  filter:brightness(1.08);
}
.card,
.link-list,
.link-row{
  max-width:100%;
  min-width:0;
}
.card{
  overflow:hidden;
}
.link-row.compact-link{
  width:100%;
  max-width:100%;
  min-width:0;
  grid-template-columns:54px minmax(0,1fr) !important;
  overflow:hidden;
}
.link-row.compact-link > div{
  min-width:0;
}
.link-row.compact-link .link-preview{
  display:block;
  width:100%;
  max-width:100%;
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.qr-modal{
  position:fixed;
  inset:0;
  z-index:120;
  display:none;
  align-items:center;
  justify-content:center;
  padding:22px;
  background:rgba(2,6,14,.72);
  backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px);
}
.qr-modal.open{
  display:flex;
}
.qr-modal-card{
  position:relative;
  width:min(420px,100%);
  border-radius:32px;
  padding:22px;
  background:
    radial-gradient(circle at 20% 0%, rgba(95,143,255,.20), transparent 18rem),
    linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.055));
  border:1px solid rgba(255,255,255,.14);
  box-shadow:0 28px 90px rgba(0,0,0,.46);
}
.qr-modal-close{
  position:absolute;
  right:16px;
  top:16px;
  width:42px;
  height:42px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,.12);
  background:rgba(255,255,255,.08);
  color:var(--text);
  font-size:24px;
  cursor:pointer;
}
.qr-modal-title{
  padding-right:48px;
  font-size:28px;
  font-weight:950;
  letter-spacing:-.04em;
}
.qr-modal-sub{
  margin-top:6px;
  color:var(--muted);
  font-size:15px;
}
.qr-modal-box{
  margin:20px auto 18px;
  width:min(300px,88vw);
  aspect-ratio:1/1;
  padding:16px;
  border-radius:28px;
  background:#fff;
  box-shadow:0 0 42px rgba(66,227,200,.16);
}
.qr-modal-box img{
  display:block;
  width:100%;
  height:100%;
  object-fit:contain;
}
.manual-copy-box{
  margin-top:16px;
  display:grid;
  gap:10px;
}
.manual-copy-title{
  color:var(--muted);
  font-size:13px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.06em;
}
.manual-copy-text{
  width:100%;
  min-height:58px;
  max-height:120px;
  box-sizing:border-box;
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  background:rgba(0,0,0,.16);
  color:var(--text);
  padding:12px 14px;
  font:600 14px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  resize:vertical;
  outline:none;
}
@media (max-width:720px){
  .link-row.compact-link{
    grid-template-columns:54px minmax(0,1fr) !important;
  }
  .hero-title{
    font-size:22px;
  }
  .big-btn{
    min-height:54px;
  }
}


.profile-grid{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}
.profile-head{display:flex;align-items:center;gap:12px;min-width:0}
.profile-flag{width:60px;height:60px;border-radius:18px;display:grid;place-items:center;font-size:34px;background:rgba(255,255,255,.07);border:1px solid var(--line);flex:0 0 auto}
.profile-title{min-width:0}
.profile-title .hero-title{font-size:36px;line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.compact-sub{margin-top:6px;color:var(--muted);font-size:15px}
.stats-card{margin-bottom:12px}
.badge{justify-self:end;width:max-content;max-width:max-content;white-space:nowrap;padding:8px 14px}
.qr-quick{--qr-size:104px;display:grid;grid-template-columns:var(--qr-size) minmax(0,1fr) 54px;gap:12px;align-items:center}
.qr-quick .qr-tile{width:var(--qr-size);height:var(--qr-size);box-sizing:border-box}
.qr-quick-copy{min-width:0;overflow:hidden}
.qr-quick .muted{font-size:15px;line-height:1.3}
.step-compact{padding:11px 12px}
.step-compact b{font-size:15px;margin:0}
.step-compact .muted{font-size:13px}
.bottom-actions{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}
.footer-note,.bottom-actions .copy-btn{border-radius:28px}
.connect-modal{
  position:fixed;inset:0;display:flex;align-items:flex-end;justify-content:center;
  background:rgba(8,10,18,.62);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  opacity:0;pointer-events:none;transition:opacity .2s ease;z-index:70;padding:14px;
}
.connect-modal.open{opacity:1;pointer-events:auto}
.connect-card{
  width:min(560px,100%);max-height:92vh;overflow:auto;border-radius:24px;
  border:1px solid rgba(255,255,255,.1);background:linear-gradient(180deg,rgba(24,29,45,.95),rgba(15,18,31,.97));
  box-shadow:0 18px 50px rgba(0,0,0,.4);padding:18px;
}
.connect-title{font-size:28px;font-weight:900;letter-spacing:-.02em}
.connect-sub{margin-top:6px;color:#b9c5e8;font-size:14px;line-height:1.35}
.app-card{margin-top:12px;border-radius:18px;border:1px solid rgba(134,167,218,.2);background:linear-gradient(170deg,rgba(31,39,58,.82),rgba(18,24,38,.88));padding:14px}
.app-card.recommended{background:linear-gradient(165deg,rgba(43,60,88,.88),rgba(18,31,51,.9));border-color:rgba(96,164,201,.42);box-shadow:inset 0 1px 0 rgba(158,210,242,.1)}
.app-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px}
.app-head{display:flex;align-items:center;gap:10px}
.app-avatar{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;flex:0 0 auto;border:1px solid rgba(255,255,255,.18);background:linear-gradient(145deg,rgba(44,56,86,.95),rgba(23,31,54,.95));overflow:hidden}
.app-avatar svg,.app-avatar img{width:100%;height:100%;display:block;object-fit:cover}
.app-icon-fallback{font-weight:700;font-size:16px;color:#dbe8ff}
.recommended .app-avatar{border-color:rgba(108,172,214,.42);background:linear-gradient(145deg,rgba(51,73,109,.95),rgba(26,38,62,.95))}
.app-pill-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0 0 12px}
.app-icons{display:contents}
.mini-app{display:flex;align-items:center;gap:7px;padding:8px 10px;border-radius:999px;background:rgba(101,154,196,.09);border:1px solid rgba(132,186,226,.24);font-size:12px;color:#dbe5ff;line-height:1;white-space:nowrap;min-width:0}
.mini-app svg,.mini-app img{width:18px;height:18px;display:block;opacity:.98;flex:0 0 auto}
.manual-section{margin-top:8px;padding:10px 12px;border-radius:12px;border:1px solid rgba(132,179,217,.2);background:rgba(75,109,149,.1)}
.manual-title{font-size:12px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:#b7c9e9;margin:0 0 8px}
.json-pill{display:flex;align-items:center;justify-content:center;width:100%;padding:10px 12px;border-radius:10px;border:1px solid rgba(152,186,255,.28);background:rgba(112,142,215,.12);color:#d8e6ff;font-size:14px;font-weight:700;text-decoration:none}
.advanced-note{margin-top:6px;font-size:12px;color:#b7c9e9;line-height:1.3}
.app-name{font-size:19px;font-weight:800}
.app-badge{font-size:12px;font-weight:800;padding:6px 10px;border-radius:999px;background:rgba(139,181,255,.15);border:1px solid rgba(139,181,255,.34);color:#dce9ff}
.app-text{font-size:14px;line-height:1.4;color:#d2defc;margin:0 0 12px}
.app-hint{margin-top:8px;font-size:12px;color:#cbd8ff;line-height:1.35}
.big-btn.full{width:100%;display:flex;align-items:center;justify-content:center}
.connect-footer{display:flex;justify-content:flex-end;margin-top:14px}
.connect-footer .copy-btn{background:rgba(255,255,255,.03);border-color:rgba(194,211,243,.24);color:#c7d6f6}
@media (max-width:430px){.app-pill-grid{grid-template-columns:1fr 1fr}.mini-app{justify-content:flex-start;padding:8px 9px;font-size:11px}}
@media (max-width:720px){.profile-title .hero-title{font-size:30px}.qr-quick{--qr-size:96px;grid-template-columns:var(--qr-size) minmax(0,1fr) 50px;gap:10px}.qr-quick .quick-title{font-size:18px}.bottom-actions{grid-template-columns:1fr}.connect-footer{justify-content:stretch}.connect-footer .copy-btn{width:100%}}

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
  window.__toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
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

async function copyText(value, okText, visibleTextareaId){
  value = value || '';
  let selected = false;
  if(visibleTextareaId){
    selected = selectVisibleText(visibleTextareaId);
  }
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(value);
      showToast(okText || 'Скопировано');
      return true;
    }
  }catch(e){}

  try{
    let area = visibleTextareaId ? document.getElementById(visibleTextareaId) : null;
    let temporary = false;
    if(!area){
      area = document.createElement('textarea');
      area.value = value;
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
      showToast(okText || 'Скопировано');
      return true;
    }
  }catch(e){}

  if(!selected && visibleTextareaId){
    selectVisibleText(visibleTextareaId);
  }
  showToast('Не удалось скопировать автоматически. Текст выделен — нажмите «Копировать» вручную.');
  return false;
}

function copyFrom(el){
  copyText(el.dataset.copy || '', el.dataset.ok || 'Скопировано', el.dataset.target || '');
  try{fetch('event',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'action=profile_copied'});}catch(e){}
}

function openConnectModal(){
  const modal = document.getElementById('connectModal');
  if(!modal) return;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden','false');
}
function closeConnectModal(){
  const modal = document.getElementById('connectModal');
  if(!modal) return;
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden','true');
}
async function copyProfile(okText){
  const profileUrl = (window.__genericSubscriptionLink || '').trim();
  if(!profileUrl){
    showToast('Ссылка профиля недоступна');
    return false;
  }
  const done = await copyText(profileUrl, okText || 'Ссылка подключения скопирована', 'subscriptionProfileLink');
  if(done){
    try{fetch('event',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'action=profile_copied'});}catch(e){}
  }
  return done;
}
async function copyHiddifySubscription(okText){
  const profileUrl = (window.__hiddifySubscriptionLink || '').trim();
  if(!profileUrl){
    showToast('Ссылка профиля недоступна');
    return false;
  }
  const done = await copyText(profileUrl, okText || 'Ссылка подключения скопирована', 'subscriptionProfileLink');
  if(done){
    try{fetch('event',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'action=profile_copied'});}catch(e){}
  }
  return done;
}
async function openHiddify(){
  const copied = await copyHiddifySubscription('Ссылка подключения скопирована. Если Hiddify не открылся — вставьте ссылку вручную.');
  if(!copied) return;
  const profileUrl = (window.__hiddifyLink || '').trim();
  const deepLink = 'hiddify://import/' + profileUrl;
  window.location.href = deepLink;
}

function showQrModal(){
  const modal = document.getElementById('qrModal');
  if(!modal) return;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden','false');
}
function hideQrModal(){
  const modal = document.getElementById('qrModal');
  if(!modal) return;
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden','true');
}
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape') hideQrModal();
  if(e.key === 'Escape') closeConnectModal();
});
document.addEventListener('DOMContentLoaded', function(){
  const codeInput = document.querySelector('input[name="code"]');
  if(!codeInput) return;
  const queryCode = new URLSearchParams(window.location.search).get('code') || '';
  const hashCode = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('code') || '';
  const code = queryCode || hashCode;
  if(code && !codeInput.value){
    codeInput.value = code;
  }
  const copyBtn = document.querySelector('button[data-copy]');
  if(copyBtn && code && !((copyBtn.dataset.copy || '').trim())){
    copyBtn.dataset.copy = code;
  }
});

</script>
"""


def render_login(error="", prefill_code=""):
    error_html = f'<div class="error">{esc(error)}</div>' if error else ""
    safe_prefill = esc(prefill_code)
    copy_btn_html = ""
    if str(prefill_code).strip():
        copy_btn_html = (
            f'<button class="copy-btn" type="button" data-copy="{safe_prefill}" '
            'onclick="copyFrom(this)">Скопировать код</button>'
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN Access</title>
{STYLE}
</head>
<body>
<div class="wrap login-wrap">
  <section class="hero">
    <h1>VPN доступ</h1>
    <div class="subtitle">Введите личный код, чтобы открыть ваш готовый профиль подключения.</div>
  </section>

  <section class="card auth-card">
    <h2 class="auth-title">Вход по коду</h2>
    <div class="auth-sub">Код выдаёт администратор. После входа вы сразу увидите кнопку «Скопировать профиль».</div>
    {error_html}
    <form method="post" action="login">
      <input class="input" name="code" value="{safe_prefill}" placeholder="Введите код доступа" autocomplete="off" required>
      <div class="auth-actions">
        {copy_btn_html}
        <button class="copy-btn primary" type="submit">Открыть профиль</button>
      </div>
    </form>
    <div class="auth-helper">Если код не принимается — проверьте раскладку и лишние пробелы.</div>
  </section>
</div>
<div class="toast" id="toast"></div>
{JS}
</body>
</html>"""


def render_profile(slug: str):
    settings = load_settings()
    user = find_user(slug)

    if not user or not user.get("enabled", True):
        return render_login("Профиль отключён или код недействителен.")

    all_stats, max_total, stats_ok = all_user_stats()
    info = all_stats.get(slug, {"up": 0, "down": 0, "total": 0, "connections": 0, "last": "—"})

    st = server_status()

    safe_slug = quote(str(slug), safe="")
    subscription_link = f"{subscription_base(settings)}/{safe_slug}.txt"
    json_link = f"{subscription_base(settings)}/{safe_slug}.json"
    fallback_link = f"{subscription_base(settings)}/{safe_slug}-8443.txt"
    fallback_json_link = f"{subscription_base(settings)}/{safe_slug}-8443.json"
    raw_link = raw_vless_from_file(settings, user)

    fallback_exists = (subscription_dir(settings) / f"{slug}-8443.txt").exists() or (subscription_dir(settings) / f"{slug}-8443.json").exists()
    json_exists = (subscription_dir(settings) / f"{slug}.json").exists()
    fallback_json_exists = (subscription_dir(settings) / f"{slug}-8443.json").exists()
    fallback_primary_link = fallback_json_link if fallback_json_exists else fallback_link
    generic_copy_link = subscription_link
    hiddify_link = subscription_link
    display_name = str(user.get("name", slug))
    hiddify_profile_name = display_name or "NeuroVPN"
    json_link_js = json.dumps(json_link, ensure_ascii=False)
    generic_copy_link_js = json.dumps(generic_copy_link, ensure_ascii=False)
    hiddify_link_js = json.dumps(hiddify_link, ensure_ascii=False)
    hiddify_profile_name_js = json.dumps(hiddify_profile_name, ensure_ascii=False)

    icon_set = load_icon_set()

    def icon_markup(icon_key: str, alt_text: str):
        icon_value = icon_set.get(icon_key, "")
        if not icon_value:
            return f"<span class='app-icon-fallback'>{esc(alt_text[:1])}</span>"
        if icon_value.startswith("data:"):
            return f"<img src='{icon_value}' alt='' loading='lazy' decoding='async'>"
        return icon_value

    hiddify_icon = icon_markup("hiddify", "Hiddify")
    happ_icon = icon_markup("happ", "Happ")
    v2rayng_icon = icon_markup("v2rayng", "v2rayNG")
    nekobox_icon = icon_markup("nekobox", "NekoBox")
    streisand_icon = icon_markup("streisand", "Streisand")

    total = int(info["total"])
    down = int(info["down"])
    up = int(info["up"])
    connections = int(info["connections"])
    last_seen = str(info["last"] or "—")
    percent = progress_percent(total, max_total)

    location = str(user.get("location") or settings.get("server_location") or "Amsterdam · NL")
    qr_v = int(time.time())

    traffic_total_text = human_bytes(total) if stats_ok else "Недоступно"
    down_text = f"↓ {human_bytes(down)}" if stats_ok else "↓ Нет данных"
    up_text = f"↑ {human_bytes(up)}" if stats_ok else "↑ Нет данных"
    stats_note = "" if stats_ok else '<div class="muted" style="margin-top:8px">Статистика Xray временно недоступна.</div>'

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(display_name)} · VPN Profile</title>
{STYLE}
</head>
<body>
<div class="wrap">
  <section class="card">
    <div class="profile-grid">
      <div class="profile-head">
        <div class="profile-flag">🇳🇱</div>
        <div class="profile-title">
          <div class="hero-title">{esc(display_name)}</div>
          <div class="compact-sub">{esc(location)}</div>
        </div>
      </div>
      <span class="badge {esc(st['class'])}">{esc(st['badge'])}</span>
    </div>
  </section>

  <section class="card stats-card">
    <div class="traffic-head"><div><div class="label">Статистика</div><div class="traffic-title">Расход трафика</div></div><div class="traffic-total">{esc(traffic_total_text)}</div></div>
    {stats_note}
    <div class="progress"><i style="width:{percent}%"></i></div>
    <div class="traffic-meta">
      <div class="metric"><div class="k">Скачано</div><div class="v">{esc(down_text)}</div></div>
      <div class="metric"><div class="k">Отправлено</div><div class="v">{esc(up_text)}</div></div>
      <div class="metric"><div class="k">30 минут</div><div class="v">{connections} conn</div></div>
      <div class="metric"><div class="k">Последний вход</div><div class="v">{esc(last_seen)}</div></div>
    </div>
  </section>

  <div class="main-cta-wrap">
    <button type="button" class="big-btn primary" onclick="openConnectModal()">🚀 Подключить VPN</button>
  </div>

  <section class="card qr-quick">
    <div class="qr-tile"><img src="qr?kind=subscription&amp;v={qr_v}" alt="QR"></div>
    <div class="qr-quick-copy">
      <div class="quick-title">QR-подключение</div>
      <div class="muted">Быстрый альтернативный импорт через камеру.</div>
    </div>
    <button type="button" class="icon-btn" onclick="showQrModal()" aria-label="Открыть QR">›</button>
  </section>

  <section class="card">
    <div class="section-title">3 шага</div>
    <div class="steps">
      <div class="step step-line step-compact"><div class="step-num">1</div><div class="step-text"><b>Скопируйте профиль</b><div class="muted">Кнопка выше.</div></div></div>
      <div class="step step-line step-compact"><div class="step-num">2</div><div class="step-text"><b>Откройте VPN-приложение</b><div class="muted">Экран импорта профиля.</div></div></div>
      <div class="step step-line step-compact"><div class="step-num">3</div><div class="step-text"><b>Импорт из буфера</b><div class="muted">Вставьте и подключитесь.</div></div></div>
    </div>
    <details style="margin-top:12px;"><summary>Не скопировалось автоматически?</summary><div class="details-inner"><div class="manual-copy-box"><div class="manual-copy-title">Основная ссылка</div><textarea id="subscriptionProfileLink" class="manual-copy-text" readonly onclick="this.select()">{esc(subscription_link)}</textarea></div></div></details>
  </section>

  <section class="card">
    <details>
      <summary>Если не получилось подключиться</summary>
      <div class="details-inner">
        <div class="details-hint">Сначала подписка, затем резерв 8443. Raw VLESS — только как крайний вариант.</div>
        <div class="actions">
          <button
            type="button"
            class="big-btn secondary"
            data-copy="{esc(subscription_link)}"
            data-ok="Подписка скопирована"
            data-target="subscriptionProfileLink"
            onclick="copyFrom(this)"
          >Скопировать подписку</button>
          {"<button type='button' class='big-btn secondary' data-copy='" + esc(fallback_primary_link) + "' data-ok='Резервный профиль 8443 скопирован' data-target='fallbackProfileLink' onclick='copyFrom(this)'>Скопировать резерв 8443</button>" if fallback_exists else ""}
          <button
            type="button"
            class="big-btn secondary"
            data-copy="{esc(raw_link)}"
            data-ok="Raw VLESS скопирован. Используйте только если JSON/подписка не импортируются."
            data-target="rawProfileLink"
            onclick="copyFrom(this)"
          >Скопировать Raw VLESS (крайний случай)</button>
        </div>
        <div class="manual-copy-box">
          <div class="manual-copy-title">Подписка</div>
          <textarea id="subscriptionProfileLinkDetails" class="manual-copy-text" readonly onclick="this.select()">{esc(subscription_link)}</textarea>
          {"<div class='manual-copy-title'>Резервная ссылка 8443</div><textarea id='fallbackProfileLink' class='manual-copy-text' readonly onclick='this.select()'>" + esc(fallback_primary_link) + "</textarea>" if fallback_exists else ""}
          <div class="manual-copy-title">Raw VLESS — только если не сработали подписка и резерв 8443</div>
          <textarea id="rawProfileLink" class="manual-copy-text" readonly onclick="this.select()">{esc(raw_link)}</textarea>
        </div>
      </div>
    </details>
    <details><summary>Дополнительные варианты</summary><div class="details-inner"><ul class="mini-list"><li>Подписка .txt — альтернативный импорт.</li><li>Резерв 8443 — если основной вариант блокируется.</li><li>Raw VLESS — ручной крайний случай.</li></ul></div></details>
  </section>

  <section class="card footer-note" style="margin-top:10px;">🔒 Соединение защищено</section>
  <div class="bottom-actions"><a class="copy-btn" href="logout">Сменить профиль</a></div>
</div>

<div class="qr-modal" id="qrModal" aria-hidden="true" onclick="if(event.target===this)hideQrModal()">
  <div class="qr-modal-card">
    <button class="qr-modal-close" type="button" onclick="hideQrModal()">×</button>
    <div class="qr-modal-title">QR для подключения</div>
    <div class="qr-modal-sub">QR содержит профиль подключения для импорта.</div>
    <div class="qr-modal-box"><img src="qr?kind=subscription&amp;v={qr_v}" alt="QR"></div>
    <button type="button" class="big-btn primary" data-copy="{esc(subscription_link)}" data-ok="Ссылка подключения скопирована" data-target="subscriptionProfileLink" onclick="copyFrom(this)">📋 Скопировать ссылку подключения</button>
  </div>
</div>
<div class="connect-modal" id="connectModal" aria-hidden="true" onclick="if(event.target===this)closeConnectModal()">
  <div class="connect-card">
    <div class="connect-title">Подключение VPN</div>
    <div class="connect-sub">Быстрое подключение через Hiddify и компактный запасной вариант ниже.</div>

    <div class="app-card recommended">
      <div class="app-top">
        <div class="app-head"><div class="app-avatar" aria-hidden="true">{hiddify_icon}</div><div class="app-name">Hiddify</div></div>
        <div class="app-badge">Рекомендуется</div>
      </div>
      <p class="app-text">Быстрый импорт профиля.</p>
      <button type="button" class="big-btn primary" onclick="openHiddify()">Открыть в Hiddify</button>
      <div class="app-hint">Если не открылось — используйте импорт из буфера.</div>
    </div>

    <div class="app-card">
      <div class="app-top">
        <div class="app-head"><div class="app-name">Другие приложения</div></div>
      </div>
      <p class="app-text">Скопируйте ссылку и импортируйте её в клиент.</p>
      <div class="app-pill-grid" aria-hidden="true"><div class="mini-app">{happ_icon}Happ</div><div class="mini-app">{v2rayng_icon}v2rayNG</div><div class="mini-app">{nekobox_icon}NekoBox</div><div class="mini-app">{streisand_icon}Streisand</div></div>
      <button type="button" class="big-btn secondary full" onclick="copyProfile('Ссылка подключения скопирована. Импортируйте в приложении.')">Скопировать ссылку</button>
      {"<div class='manual-section'><div class='manual-title'>Ручная настройка</div><a class='json-pill' href='" + esc(json_link) + "' target='_blank' rel='noopener'>JSON-конфиг</a><div class='advanced-note'>Для ручного импорта.</div></div>" if json_exists else ""}
    </div>
    <div class="connect-footer">
      <button type="button" class="copy-btn" onclick="closeConnectModal()">Закрыть</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>window.__hiddifyLink = {hiddify_link_js}; window.__hiddifyProfileName = {hiddify_profile_name_js}; window.__hiddifySubscriptionLink = {hiddify_link_js}; window.__genericSubscriptionLink = {generic_copy_link_js}; window.__jsonFallbackLink = {json_link_js};</script>
{JS}
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, data, content_type="text/html; charset=utf-8", status_code=200, headers=None):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        if headers:
            items = headers.items() if hasattr(headers, "items") else headers
            for k, v in items:
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_html(self, text, status_code=200, headers=None):
        self.send_bytes(text.encode("utf-8"), "text/html; charset=utf-8", status_code, headers)

    def send_json(self, data, status_code=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(raw, "application/json; charset=utf-8", status_code)

    def redirect(self, location="./", headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        if headers:
            items = headers.items() if hasattr(headers, "items") else headers
            for k, v in items:
                self.send_header(k, v)
        self.end_headers()

    def get_cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.strip().split("=", 1)
            if k == name:
                return v
        return ""

    def current_slug(self):
        return verify_token(self.get_cookie(COOKIE_NAME))

    def make_cookie_header(self, slug):
        token = make_token(slug)
        return [
            ("Set-Cookie", f"{COOKIE_NAME}={token}; Path={cookie_path()}; Max-Age={COOKIE_MAX_AGE}; HttpOnly; Secure; SameSite=Lax")
        ]

    def clear_cookie_headers_for_paths(self, paths):
        # Some proxies/browsers can behave poorly with repeated Set-Cookie on redirects.
        # Keep the generic helper, but logout itself clears cookies in two redirect steps:
        # first the path-scoped cookie, then the old root-scoped cookie.
        unique_paths = []
        for p in paths:
            if p and p not in unique_paths:
                unique_paths.append(p)
        headers = []
        for p in unique_paths:
            headers.append(("Set-Cookie", f"{COOKIE_NAME}=; Path={p}; Max-Age=0; HttpOnly; Secure; SameSite=Lax"))
        return headers

    def clear_cookie_headers(self):
        # Delete both new path-scoped cookie and old root-scoped cookie from previous versions.
        return self.clear_cookie_headers_for_paths([cookie_path(), "/"])

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        return parse_qs(raw)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            return self.send_json({"ok": True, "service": "vpn-user", "version": VERSION})

        if path == "/favicon.ico":
            return self.send_bytes(b"", "image/x-icon", 204)

        if path == "/logout":
            # Step 1: delete the current path-scoped cookie.
            # Then redirect to /logout-root to delete legacy Path=/ cookie from older builds.
            return self.redirect("logout-root", headers=self.clear_cookie_headers_for_paths([cookie_path()]))

        if path == "/logout-root":
            # Step 2: delete the old root-scoped cookie.
            return self.redirect("./?logged_out=1", headers=self.clear_cookie_headers_for_paths(["/"]))

        if path == "/qr":
            slug = self.current_slug()
            if not slug:
                return self.send_html(render_login("Сессия закончилась. Введите код заново."), 401)
            user = find_user(slug)
            if not user or not user.get("enabled", True):
                return self.send_html(render_login("Профиль недоступен."), 401)
            settings = load_settings()
            kind = parse_qs(parsed.query).get("kind", ["subscription"])[0]
            safe_slug = quote(str(slug), safe='')
            if kind in ("sub", "subscription"):
                text = f"{subscription_base(settings)}/{safe_slug}.txt"
            elif kind in ("vless", "raw"):
                text = raw_vless_from_file(settings, user)
            elif kind in ("fallback", "8443"):
                text = f"{subscription_base(settings)}/{safe_slug}-8443.json"
            elif kind in ("json", "xray"):
                text = f"{subscription_base(settings)}/{safe_slug}.json"
            else:
                text = f"{subscription_base(settings)}/{safe_slug}.txt"
            svg = qr_svg(text)
            return self.send_bytes(svg, "image/svg+xml; charset=utf-8", 200)

        if path == "/":
            ensure_access_codes(load_users())
            slug = self.current_slug()
            if slug:
                return self.send_html(render_profile(slug))
            prefill_code = (parse_qs(parsed.query).get("code", [""])[0] or "").strip()
            return self.send_html(render_login(prefill_code=prefill_code))

        return self.redirect("./")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/login":
            ensure_access_codes(load_users())
            form = self.read_form()
            code = (form.get("code", [""])[0] or "").strip()
            user = find_user_by_code(code)
            if not user:
                return self.send_html(render_login("Неверный код доступа.", prefill_code=code))
            slug = str(user.get("slug", ""))
            return self.redirect("./", headers=self.make_cookie_header(slug))

        return self.redirect("./")

    def log_message(self, fmt, *args):
        super().log_message(fmt, *args)


if __name__ == "__main__":
    ensure_access_codes(load_users())
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"vpn-user listening on http://{HOST}:{PORT}")
    server.serve_forever()
