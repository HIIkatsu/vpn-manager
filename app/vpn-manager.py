#!/usr/bin/env python3
"""Clean VPN manager CLI backed by SQLite3."""

import argparse
import base64
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

BASE = Path("/root/vpn-manager")
SETTINGS = BASE / "settings.json"
ROUTES = BASE / "routes.json"
PENDING = BASE / "admin_pending_changes.json"
BACKUPS = BASE / "backups"
DB_PATH = BASE / "config/database.db"

XRAY_CONFIG = Path("/usr/local/etc/xray/config.json")
NGINX_SNIPPET = Path("/etc/nginx/snippets/vpn-subscriptions.conf")
NGINX_USER_PAGES_SNIPPET = Path("/etc/nginx/snippets/vpn-user-pages.conf")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def sh(cmd, check=True, capture=False, timeout=180):
    print("+", " ".join(map(str, cmd)), file=sys.stderr)
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, timeout=timeout)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def load_db_users(enabled_only=False):
    with get_db() as conn:
        if enabled_only:
            rows = conn.execute("SELECT username as slug, uuid, enabled, token FROM users WHERE enabled = 1").fetchall()
        else:
            rows = conn.execute("SELECT username as slug, uuid, enabled, token FROM users").fetchall()
        return [dict(r) for r in rows]


def now():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def mark_pending_change(action, slug=""):
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else {"changes": []}
    except Exception:
        data = {"changes": []}
    changes = data.get("changes", [])
    changes = [item for item in changes if not (str(item.get("action")) == str(action) and str(item.get("slug")) == str(slug))]
    changes.append({"action": str(action), "slug": str(slug), "ts": int(time.time())})
    data["changes"] = changes[-50:]
    data["updated_at"] = int(time.time())
    PENDING.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_pending_changes():
    try:
        if PENDING.exists():
            PENDING.unlink()
    except Exception:
        pass


def validate_slug(slug: str) -> str:
    slug = str(slug or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise SystemExit("Bad slug. Use 2-32 chars: a-z, 0-9, _, -. Must start with a-z/0-9.")
    return slug


def slugify(name: str) -> str:
    value = str(name or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яё_-]+", "-", value, flags=re.I)
    value = re.sub(r"-+", "-", value).strip("-_")
    ascii_value = re.sub(r"[^a-z0-9_-]", "", value)
    if not ascii_value:
        ascii_value = "user-" + uuid.uuid4().hex[:8]
    return validate_slug(ascii_value[:32])


def client_host(settings):
    return settings.get("client_host") or settings.get("server_ip") or settings["domain"]


def public_port(settings):
    return int(settings.get("public_port", 443))


def fallback_port(settings):
    return int(settings.get("fallback_port", settings["xray_port"]))


def build_xray(settings, users):
    protect_private_ips = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"]
    server_ip = str(settings.get("server_ip", "")).strip()
    protect_rules = [{"type": "field", "inboundTag": ["vless-reality"], "ip": protect_private_ips, "outboundTag": "block"}]
    
    if server_ip:
        protect_rules.append({
            "type": "field",
            "inboundTag": ["vless-reality"],
            "ip": [server_ip],
            "port": "22,10085,8010,8011",
            "outboundTag": "block",
        })

    return {
        "log": {"loglevel": settings.get("xray_loglevel", "warning")},
        "api": {"tag": "api", "services": ["StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True, "statsInboundDownlink": True,
                "statsOutboundUplink": True, "statsOutboundDownlink": True,
            },
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                *protect_rules,
                {"type": "field", "domain": ["geosite:google", "geosite:openai", "geosite:anthropic", "geosite:netflix", "domain:chatgpt.com"], "outboundTag": "proxy-ipv6"},
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
            ],
        },
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": int(settings.get("xray_api_port", 10085)),
                "protocol": "dokodemo-door",
                "tag": "api",
                "settings": {"address": "127.0.0.1"},
            },
            {
                "listen": settings.get("xray_listen", "0.0.0.0"),
                "port": int(settings["xray_port"]),
                "protocol": "vless",
                "tag": "vless-reality",
                "settings": {
                    "clients": [
                        {"id": u["uuid"], "flow": settings.get("flow", "xtls-rprx-vision"), "email": u["slug"]}
                        for u in users if u.get("enabled", 1) == 1
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "sockopt": {"tcpNoDelay": True, "tcpKeepAliveIdle": 30, "tcpKeepAliveInterval": 30},
                    "realitySettings": {
                        "show": False,
                        "dest": settings["reality_dest"],
                        "xver": 0,
                        "serverNames": [settings["sni"]],
                        "privateKey": settings["private_key"],
                        "shortIds": [settings["short_id"]],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "socks", "tag": "proxy-ipv6", "settings": {"servers": [{"address": "45.153.20.214", "port": 10121, "users": [{"user": "j0S6zv", "pass": "Ev17jc"}]}]}},
            {"protocol": "blackhole", "tag": "block"},
        ],
    }


def build_client_routing(routes):
    rules = [
        {"type": "field", "ip": routes.get("private_ips", []), "outboundTag": "direct", "ruleTag": "Direct private networks"},
        {"type": "field", "protocol": routes.get("direct_protocols", ["bittorrent"]), "outboundTag": "direct", "ruleTag": "Direct selected protocols"},
        {"type": "field", "domain": routes.get("direct_domains", []), "outboundTag": "direct", "ruleTag": "Direct Russian domains"},
        {"type": "field", "ip": routes.get("direct_ips", []), "outboundTag": "direct", "ruleTag": "Direct Russian IPs"},
        {"type": "field", "network": "tcp,udp", "outboundTag": routes.get("default_outbound_tag", "proxy"), "ruleTag": "Default route through VPN proxy"},
    ]
    routing = {
        "domainStrategy": routes.get("domain_strategy", "IPIfNonMatch"),
        "domainMatcher": routes.get("domain_matcher", "hybrid"),
        "balancers": [],
        "rules": rules,
        "name": routes.get("name", "RU direct, others proxy"),
    }
    return base64.b64encode(json.dumps(routing, separators=(",", ":")).encode("utf-8")).decode("ascii")


def vless_link(settings, user, port=None, title=None):
    port = int(port or public_port(settings))
    profile_title = title or f"VPN-{user['slug']}-Direct"
    query = {
        "type": "tcp", "encryption": "none", "security": "reality",
        "sni": settings["sni"], "alpn": "h2,http/1.1", "fp": settings.get("fingerprint", "chrome"),
        "pbk": settings["public_key"], "sid": settings["short_id"], "flow": settings.get("flow", "xtls-rprx-vision"),
    }
    qs = "&".join(f"{k}={quote(str(v), safe='-_~.')}" for k, v in query.items() if v)
    return f"vless://{user['uuid']}@{client_host(settings)}:{port}?{qs}#{quote(profile_title)}"


def _render_subscription_files(settings, users):
    routes = load(ROUTES)
    p_port = public_port(settings)
    files = {"routing.json": json.dumps(json.loads(base64.b64decode(build_client_routing(routes)).decode("utf-8")), ensure_ascii=False, indent=2) + "\n"}

    for user in users:
        if user.get("enabled", 1) == 0:
            continue
        slug = user["slug"]
        token = user.get("token", slug)

        # 1. Профиль DIRECT (Прямой Reality до сервера)
        direct = vless_link(settings, user, p_port, "🇫🇮 Direct")
        # 2. Профиль SMART (С обходом блокировок Гугла/OpenAI на сервере)
        smart = vless_link(settings, user, p_port, "🇺🇸 USA warp")
        # 3. Профиль EMERGENCY (Задел под Cloudflare Workers для обхода белых списков)
        emergency = direct.split("@")[0] + "@84.252.75.36:" + direct.split("@")[1].split(":", 1)[1].split("#")[0] + "#%F0%9F%87%B7%F0%9F%87%BA%20%D0%A7%D0%B5%D0%B1%D1%83%D1%80%D0%BD%D0%B5%D1%82%20%28FirstByte%20L4%29"

        # Склеиваем три конфига в единый бандл подписки
        subscription_bundle = f"{direct}\n{smart}\n{emergency}\n"
        
        # Записываем файлы подписок по токену, а не по публичному юзернейму
        files[f"{token}.txt"] = subscription_bundle
        files[f"{token}.json"] = json.dumps({"remarks": slug, "outbounds": []}, indent=2) + "\n"

    return files


def write_subscriptions(settings, users):
    subdir = Path(settings["subscription_dir"])
    subdir.mkdir(parents=True, exist_ok=True)
    files = _render_subscription_files(settings, users)
    target_names = set(files.keys())
    
    for path in [p for p in subdir.iterdir() if p.is_file()]:
        if path.name not in target_names and path.name != "routing.json":
            path.unlink(missing_ok=True)

    for filename, content in files.items():
        atomic_write_text(subdir / filename, content, mode=0o644)
    os.chmod(subdir, 0o755)


def public_user_path(settings):
    sub = str(settings.get("subscription_path", "vpn")).strip("/")
    return "vpn-user-" + sub.split("vpn-", 1)[1] if sub.startswith("vpn-") else sub + "-user"


def build_user_pages_snippet(settings):
    path = public_user_path(settings)
    domain = str(settings["domain"]).strip("/")
    return f"location = /{path} {{\n    return 302 https://{domain}/{path}/;\n}}\n\nlocation /{path}/ {{\n    proxy_pass http://127.0.0.1:8011/;\n    proxy_http_version 1.1;\n    proxy_set_header Host $host;\n    proxy_set_header X-Real-IP $remote_addr;\n    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n    proxy_set_header X-Forwarded-Proto $scheme;\n}}\n"


def build_snippet(settings, routes):
    routing_b64 = build_client_routing(routes)
    profile_title_b64 = "base64:" + base64.b64encode(settings.get("profile_title", "AnKo VPN").encode("utf-8")).decode("ascii")
    return f'location /{str(settings["subscription_path"]).strip("/")}/ {{\n    alias {str(settings["subscription_dir"]).rstrip("/")}/;\n    types {{\n        text/plain txt;\n        application/json json;\n    }}\n    default_type text/plain;\n    add_header profile-title "{profile_title_b64}" always;\n    add_header profile-update-interval "{int(settings.get("profile_update_interval", 12))}" always;\n    add_header update-always "true" always;\n    add_header routing "{routing_b64}" always;\n    add_header Cache-Control "no-store" always;\n}}\n'


def atomic_write_text(path: Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def atomic_write_json(path: Path, data, mode=0o644):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def backup(settings):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup_dir = BACKUPS / now()
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in [XRAY_CONFIG, NGINX_SNIPPET, NGINX_USER_PAGES_SNIPPET, SETTINGS, ROUTES]:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    print(f"Backup: {backup_dir}")


def wait_for_local_port(port, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex(("127.0.0.1", int(port))) == 0:
                return True
        time.sleep(0.4)
    return False


def apply_config(dry_run=False):
    settings = load(SETTINGS)
    users = load_db_users()
    routes = load(ROUTES)

    xray_config = build_xray(settings, users)
    snippet = build_snippet(settings, routes)
    user_pages_snippet = build_user_pages_snippet(settings)

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
        json.dump(xray_config, f)
        temp_xray = f.name
    try:
        sh(["xray", "-test", "-config", temp_xray], timeout=60)
    finally:
        Path(temp_xray).unlink(missing_ok=True)

    if dry_run:
        print("Dry run OK. Nothing changed.")
        return

    backup(settings)
    atomic_write_text(NGINX_SNIPPET, snippet)
    atomic_write_json(XRAY_CONFIG, xray_config)
    
    reload_res = sh(["systemctl", "reload", "xray"], check=False, capture=True, timeout=120)
    if reload_res.returncode != 0:
        sh(["systemctl", "restart", "xray"], timeout=120)
        
    if not wait_for_local_port(settings["xray_port"]):
        raise RuntimeError(f"Xray port {settings['xray_port']} is not listening")

    write_subscriptions(settings, users)
    sh(["systemctl", "reload", "nginx"], timeout=60)
    print("Applied OK")
    clear_pending_changes()


def list_users():
    for u in load_db_users():
        state = "ON " if u["enabled"] == 1 else "OFF"
        print(f"{state} {u['slug']:<16} SubToken: {u['token']}  UUID: {u['uuid']}")


def add_user(name, slug=None):
    slug = validate_slug(slug) if slug else slugify(name)
    u_uuid = str(uuid.uuid4())
    token = uuid.uuid4().hex  # Секретный токен для ссылки подписки

    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, uuid, token, enabled, comment) VALUES (?, ?, ?, 1, ?)",
                (slug, u_uuid, token, name)
            )
            conn.commit()
            mark_pending_change("add", slug)
            print(f"✅ Added to SQLite: {slug}\nUUID: {u_uuid}\nSubToken: {token}\nRun: vpn-manager apply")
        except sqlite3.IntegrityError:
            print(f"❌ User with slug '{slug}' already exists in database.")


def set_enabled(slug, enabled):
    slug = validate_slug(slug)
    with get_db() as conn:
        res = conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (1 if enabled else 0, slug))
        conn.commit()
        if res.rowcount == 0:
            raise SystemExit(f"No such user: {slug}")
    mark_pending_change("toggle", slug)
    print(f"✅ {slug} {'enabled' if enabled else 'disabled'}. Run: vpn-manager apply")


def delete_user(slug):
    slug = validate_slug(slug)
    with get_db() as conn:
        res = conn.execute("DELETE FROM users WHERE username = ?", (slug,))
        conn.commit()
        if res.rowcount == 0:
            raise SystemExit(f"No such user: {slug}")
    mark_pending_change("delete", slug)
    print(f"✅ Deleted from SQLite: {slug}. Run: vpn-manager apply")


def main():
    parser = argparse.ArgumentParser(prog="vpn-manager")
    sub = parser.add_subparsers(dest="cmd", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("list-users")
    add_parser = sub.add_parser("add-user")
    add_parser.add_argument("name")
    add_parser.add_argument("--slug")
    disable_parser = sub.add_parser("disable-user")
    disable_parser.add_argument("slug")
    enable_parser = sub.add_parser("enable-user")
    enable_parser.add_argument("slug")
    delete_parser = sub.add_parser("delete-user")
    delete_parser.add_argument("slug")

    args = parser.parse_args()
    if args.cmd == "apply":
        apply_config(args.dry_run)
    elif args.cmd == "list-users":
        list_users()
    elif args.cmd == "add-user":
        add_user(args.name, args.slug)
    elif args.cmd == "disable-user":
        set_enabled(args.slug, False)
    elif args.cmd == "enable-user":
        set_enabled(args.slug, True)
    elif args.cmd == "delete-user":
        delete_user(args.slug)


if __name__ == "__main__":
    main()