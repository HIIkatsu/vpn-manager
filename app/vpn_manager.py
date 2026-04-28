#!/usr/bin/env python3
"""Clean VPN manager CLI for Xray/VLESS/REALITY.

Source of truth:
  /root/vpn-manager/settings.json
  /root/vpn-manager/users.json
  /root/vpn-manager/routes.json

`apply` deterministically regenerates:
  /usr/local/etc/xray/config.json
  /etc/nginx/snippets/vpn-subscriptions.conf
  subscription files in settings.subscription_dir
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

BASE = Path("/root/vpn-manager")
SETTINGS = BASE / "settings.json"
USERS = BASE / "users.json"
ROUTES = BASE / "routes.json"
BACKUPS = BASE / "backups"

XRAY_CONFIG = Path("/usr/local/etc/xray/config.json")
NGINX_SNIPPET = Path("/etc/nginx/snippets/vpn-subscriptions.conf")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def sh(cmd, check=True, capture=False, timeout=180):
    print("+", " ".join(map(str, cmd)), file=sys.stderr)
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, timeout=timeout)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def now():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def validate_slug(slug: str) -> str:
    slug = str(slug or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise SystemExit("Bad slug. Use 2-32 chars: a-z, 0-9, _, -. Must start with a-z/0-9.")
    return slug


def slugify(name: str) -> str:
    value = str(name or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яё_-]+", "-", value, flags=re.I)
    value = re.sub(r"-+", "-", value).strip("-_")
    # Keep non-latin names from becoming invalid by falling back to uuid suffix.
    ascii_value = re.sub(r"[^a-z0-9_-]", "", value)
    if not ascii_value:
        ascii_value = "user-" + uuid.uuid4().hex[:8]
    return validate_slug(ascii_value[:32])


def users_list(users_doc):
    items = users_doc.get("users", [])
    if not isinstance(items, list):
        raise SystemExit("users.json must contain a list at key 'users'")
    return items


def enabled_users(users_doc):
    return [u for u in users_list(users_doc) if u.get("enabled", True)]


def validate_users(users_doc):
    seen_slugs = set()
    seen_uuids = set()
    for user in users_list(users_doc):
        slug = validate_slug(user.get("slug", ""))
        user["slug"] = slug
        uid = str(user.get("uuid", "")).strip()
        try:
            uuid.UUID(uid)
        except Exception:
            raise SystemExit(f"Bad UUID for {slug}: {uid}")
        if slug in seen_slugs:
            raise SystemExit(f"Duplicate slug: {slug}")
        if uid in seen_uuids:
            raise SystemExit(f"Duplicate UUID: {uid}")
        seen_slugs.add(slug)
        seen_uuids.add(uid)


def client_host(settings):
    # Keep old behavior by default: use server_ip if present. Domain can be selected later by setting client_host.
    return settings.get("client_host") or settings.get("server_ip") or settings["domain"]


def public_port(settings):
    return int(settings.get("public_port", 443))


def fallback_port(settings):
    return int(settings.get("fallback_port", settings["xray_port"]))


def build_xray(settings, users_doc):
    return {
        "log": {"loglevel": settings.get("xray_loglevel", "warning")},
        "api": {"tag": "api", "services": ["StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
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
                        for u in enabled_users(users_doc)
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "sockopt": {
                        "tcpNoDelay": True,
                        "tcpKeepAliveIdle": 30,
                        "tcpKeepAliveInterval": 30,
                    },
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
            {"protocol": "blackhole", "tag": "block"},
        ],
    }


def build_client_routing(routes):
    rules = [
        {
            "type": "field",
            "ip": routes.get("private_ips", []),
            "outboundTag": "direct",
            "__name__": "Direct private networks",
        },
        {
            "type": "field",
            "protocol": routes.get("direct_protocols", ["bittorrent"]),
            "outboundTag": "direct",
            "__name__": "Direct selected protocols",
        },
        {
            "type": "field",
            "domain": routes.get("direct_domains", []),
            "ip": routes.get("direct_ips", []),
            "outboundTag": "direct",
            "__name__": "Direct Russian services",
        },
    ]
    routing = {
        "domainStrategy": routes.get("domain_strategy", "IPIfNonMatch"),
        "domainMatcher": routes.get("domain_matcher", "hybrid"),
        "balancers": [],
        "rules": rules,
        "name": routes.get("name", "RU direct, others proxy"),
    }
    raw = json.dumps(routing, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def vless_link(settings, user, port=None, title=None):
    port = int(port or public_port(settings))
    profile_title = title or user.get("title") or settings.get("profile_title") or f"VPN {user['slug']}"
    encoded_title = quote(profile_title)
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
    qs = "&".join(f"{k}={quote(str(v), safe='-_~.') }" for k, v in query.items() if v)
    return f"vless://{user['uuid']}@{client_host(settings)}:{port}?{qs}#{encoded_title}"




def client_routing_object(routes):
    return json.loads(base64.b64decode(build_client_routing(routes)).decode("utf-8"))


def vless_outbound(settings, user, port=None, tag="proxy"):
    port = int(port or public_port(settings))
    reality = {
        "show": False,
        "fingerprint": settings.get("fingerprint", "chrome"),
        "serverName": settings["sni"],
        "publicKey": settings["public_key"],
        "shortId": settings["short_id"],
        "spiderX": settings.get("spider_x", "/"),
    }
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": client_host(settings),
                    "port": port,
                    "users": [
                        {
                            "id": user["uuid"],
                            "encryption": "none",
                            "flow": settings.get("flow", "xtls-rprx-vision"),
                        }
                    ],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": reality,
        },
    }


def client_config_json(settings, routes, user, port=None):
    """Full Xray client config. Unlike a vless:// URI, this preserves routing rules."""
    routing = client_routing_object(routes)
    profile_name = user.get("title") or settings.get("profile_title") or "NeuroSMM VPN"
    # With proxy as the first outbound, traffic not matched by RU/direct rules goes through VPN.
    return {
        # Client apps may use one of these metadata fields as the imported profile name.
        # Xray-compatible parsers ignore unknown top-level fields.
        "remarks": profile_name,
        "name": profile_name,
        "title": profile_name,
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": int(settings.get("client_socks_port", 10808)),
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
            {
                "tag": "http-in",
                "listen": "127.0.0.1",
                "port": int(settings.get("client_http_port", 10809)),
                "protocol": "http",
                "settings": {},
            },
        ],
        "outbounds": [
            # Some mobile clients use the first outbound tag as a display name when importing full JSON.
            vless_outbound(settings, user, port, profile_name),
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": routing,
    }


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o644)

def build_snippet(settings, routes):
    routing_b64 = build_client_routing(routes)
    profile_title = settings.get("profile_title", "NeuroSMM VPN")
    profile_title_b64 = "base64:" + base64.b64encode(profile_title.encode("utf-8")).decode("ascii")
    subscription_path = str(settings["subscription_path"]).strip("/")
    subscription_dir = str(settings["subscription_dir"]).rstrip("/")
    return f'''location /{subscription_path}/ {{
    alias {subscription_dir}/;
    types {{
        text/plain txt;
        application/json json;
    }}
    default_type text/plain;

    add_header profile-title "{profile_title_b64}" always;
    add_header profile-update-interval "{int(settings.get('profile_update_interval', 12))}" always;
    add_header update-always "true" always;
    add_header routing "{routing_b64}" always;
    add_header Cache-Control "no-store" always;
}}
'''


def base_slug_from_sub_file(path: Path):
    stem = path.stem
    for suffix in ("-443", "-8443"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def write_subscriptions(settings, users_doc):
    subdir = Path(settings["subscription_dir"])
    subdir.mkdir(parents=True, exist_ok=True)
    routes = load(ROUTES)

    active = {u["slug"]: u for u in enabled_users(users_doc)}
    all_slugs = {u["slug"] for u in users_list(users_doc)}

    # Remove stale enabled files for disabled/deleted users. Backups already exist before apply.
    for path in list(subdir.glob("*.txt")) + list(subdir.glob("*.json")):
        base_slug = base_slug_from_sub_file(path)
        # routing.json is global, not user-specific.
        if path.name == "routing.json":
            continue
        if base_slug not in active:
            path.unlink(missing_ok=True)

    for path in list(subdir.glob("*.txt.disabled")) + list(subdir.glob("*.json.disabled")):
        base_slug = base_slug_from_sub_file(Path(path.name.replace(".disabled", "")))
        if base_slug not in all_slugs:
            path.unlink(missing_ok=True)

    p_port = public_port(settings)
    f_port = fallback_port(settings)

    # Global routing file for clients/admin diagnostics.
    write_json(subdir / "routing.json", client_routing_object(routes))

    for slug, user in active.items():
        main = vless_link(settings, user, p_port, user.get("title") or settings.get("profile_title"))
        (subdir / f"{slug}.txt").write_text(main + "\n", encoding="utf-8")
        os.chmod(subdir / f"{slug}.txt", 0o644)
        write_json(subdir / f"{slug}.json", client_config_json(settings, routes, user, p_port))

        (subdir / f"{slug}-{p_port}.txt").write_text(
            vless_link(settings, user, p_port, f"{settings.get('profile_title', 'VPN')} {p_port}") + "\n",
            encoding="utf-8",
        )
        os.chmod(subdir / f"{slug}-{p_port}.txt", 0o644)
        write_json(subdir / f"{slug}-{p_port}.json", client_config_json(settings, routes, user, p_port))

        if f_port != p_port:
            (subdir / f"{slug}-{f_port}.txt").write_text(
                vless_link(settings, user, f_port, f"{settings.get('profile_title', 'VPN')} fallback {f_port}") + "\n",
                encoding="utf-8",
            )
            os.chmod(subdir / f"{slug}-{f_port}.txt", 0o644)
            write_json(subdir / f"{slug}-{f_port}.json", client_config_json(settings, routes, user, f_port))

    os.chmod(subdir, 0o755)

def backup(settings):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup_dir = BACKUPS / now()
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in [XRAY_CONFIG, NGINX_SNIPPET, SETTINGS, USERS, ROUTES]:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    subdir = Path(settings["subscription_dir"])
    if subdir.exists():
        with tarfile.open(backup_dir / "subscriptions.tar.gz", "w:gz") as tar:
            tar.add(subdir, arcname=subdir.name)
    print(f"Backup: {backup_dir}")
    return backup_dir


def atomic_write_text(path: Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def atomic_write_json(path: Path, data, mode=0o644):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def test_xray_config(config):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
        temp_xray = f.name
    try:
        sh(["xray", "-test", "-config", temp_xray], timeout=60)
    finally:
        Path(temp_xray).unlink(missing_ok=True)


def apply_config(dry_run=False):
    settings = load(SETTINGS)
    users_doc = load(USERS)
    routes = load(ROUTES)
    validate_users(users_doc)

    xray_config = build_xray(settings, users_doc)
    snippet = build_snippet(settings, routes)
    test_xray_config(xray_config)
    print(f"Enabled users: {len(enabled_users(users_doc))}/{len(users_list(users_doc))}")
    print(f"Client routing base64 length: {len(build_client_routing(routes))}")

    if dry_run:
        print("Dry run OK. Nothing changed.")
        return

    old_xray = XRAY_CONFIG.read_text(encoding="utf-8") if XRAY_CONFIG.exists() else ""
    old_snippet = NGINX_SNIPPET.read_text(encoding="utf-8") if NGINX_SNIPPET.exists() else ""

    backup(settings)

    try:
        atomic_write_text(NGINX_SNIPPET, snippet)
        sh(["nginx", "-t"], timeout=60)
    except Exception:
        if old_snippet:
            atomic_write_text(NGINX_SNIPPET, old_snippet)
        raise

    try:
        atomic_write_json(XRAY_CONFIG, xray_config)
        sh(["systemctl", "restart", "xray"], timeout=120)
        xray_active = sh(["systemctl", "is-active", "xray"], check=False, capture=True, timeout=30).stdout.strip()
        if xray_active != "active":
            raise RuntimeError("xray is not active after restart")
        port_check = sh(["bash", "-lc", f"ss -lntp | grep -q ':{int(settings['xray_port'])}'"], check=False, timeout=30)
        if port_check.returncode != 0:
            raise RuntimeError(f"xray active but port {settings['xray_port']} is not listening")
    except Exception:
        print("Xray apply failed. Restoring previous Xray config.", file=sys.stderr)
        if old_xray:
            atomic_write_text(XRAY_CONFIG, old_xray)
            sh(["systemctl", "restart", "xray"], check=False, timeout=120)
        if old_snippet:
            atomic_write_text(NGINX_SNIPPET, old_snippet)
            sh(["nginx", "-t"], check=False, timeout=60)
        raise

    write_subscriptions(settings, users_doc)
    sh(["systemctl", "reload", "nginx"], timeout=60)
    nginx_active = sh(["systemctl", "is-active", "nginx"], check=False, capture=True, timeout=30).stdout.strip()
    if nginx_active != "active":
        raise SystemExit("ERROR: nginx is not active after reload")

    print("Applied OK")
    print_links()


def print_links():
    settings = load(SETTINGS)
    users_doc = load(USERS)
    base_url = f"https://{settings['domain']}/{str(settings['subscription_path']).strip('/')}"
    for u in users_list(users_doc):
        state = "ON " if u.get("enabled", True) else "OFF"
        slug = u["slug"]
        print(f"{state} {u.get('name', slug):<16} {base_url}/{quote(slug, safe='')}.txt")


def list_users():
    users_doc = load(USERS)
    validate_users(users_doc)
    for u in users_list(users_doc):
        state = "ON " if u.get("enabled", True) else "OFF"
        print(f"{state} {u['slug']:<16} {u.get('name', u['slug']):<20} {u['uuid']}")


def add_user(name, slug=None):
    users_doc = load(USERS)
    validate_users(users_doc)
    slug = validate_slug(slug) if slug else slugify(name)
    if any(u["slug"] == slug for u in users_list(users_doc)):
        raise SystemExit(f"Slug already exists: {slug}")
    new_user = {
        "name": str(name).strip(),
        "slug": slug,
        "uuid": str(uuid.uuid4()),
        "enabled": True,
        "title": load(SETTINGS).get("profile_title", "NeuroSMM VPN"),
    }
    users_doc["users"].append(new_user)
    save(USERS, users_doc)
    print(f"Added: {new_user['name']}")
    print(f"Slug: {slug}")
    print(f"UUID: {new_user['uuid']}")
    print("Run: vpn-manager apply")


def set_enabled(slug, enabled):
    slug = validate_slug(slug)
    users_doc = load(USERS)
    found = False
    for u in users_list(users_doc):
        if u["slug"] == slug:
            u["enabled"] = bool(enabled)
            found = True
            break
    if not found:
        raise SystemExit(f"No such user: {slug}")
    save(USERS, users_doc)
    print(f"{slug}: {'enabled' if enabled else 'disabled'}")
    print("Run: vpn-manager apply")


def delete_user(slug):
    slug = validate_slug(slug)
    users_doc = load(USERS)
    before = len(users_list(users_doc))
    users_doc["users"] = [u for u in users_list(users_doc) if u["slug"] != slug]
    if len(users_doc["users"]) == before:
        raise SystemExit(f"No such user: {slug}")
    save(USERS, users_doc)
    print(f"Deleted from users.json: {slug}")
    print("Run: vpn-manager apply")


def status():
    for service in ["xray", "nginx", "vpn-admin", "vpn-user"]:
        result = sh(["systemctl", "is-active", service], check=False, capture=True, timeout=20)
        print(f"{service}: {result.stdout.strip() or 'unknown'}")
    sh(["xray", "-test", "-config", str(XRAY_CONFIG)], check=False, timeout=60)
    sh(["nginx", "-t"], check=False, timeout=60)
    sh(["ss", "-lntp"], check=False, timeout=30)


def check_user(slug):
    slug = validate_slug(slug)
    settings = load(SETTINGS)
    users_doc = load(USERS)
    user = next((u for u in users_list(users_doc) if u["slug"] == slug), None)
    print(f"users.json: {'present' if user else 'missing'}")
    if user:
        print(f"enabled: {bool(user.get('enabled', True))}")
        print(f"uuid: {user.get('uuid')}")
        print(f"primary link would be: {vless_link(settings, user, public_port(settings))}")
        print(f"fallback link would be: {vless_link(settings, user, fallback_port(settings))}")
    subdir = Path(settings["subscription_dir"])
    names = [
        f"{slug}.txt", f"{slug}.json",
        f"{slug}-{public_port(settings)}.txt", f"{slug}-{public_port(settings)}.json",
        f"{slug}-{fallback_port(settings)}.txt", f"{slug}-{fallback_port(settings)}.json",
        "routing.json",
    ]
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = subdir / name
        print(f"{path}: {'exists' if path.exists() else 'missing'}")
        if path.exists():
            if path.suffix == ".txt":
                print(path.read_text(encoding="utf-8").strip())
            else:
                print(f"json size: {path.stat().st_size} bytes")
    if XRAY_CONFIG.exists():
        cfg = load(XRAY_CONFIG)
        clients = []
        for inbound in cfg.get("inbounds", []):
            clients.extend(inbound.get("settings", {}).get("clients", []))
        print("xray config client:", "present" if any(c.get("email") == slug for c in clients) else "missing")


def main():
    parser = argparse.ArgumentParser(prog="vpn-manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--dry-run", action="store_true")

    sub.add_parser("links")
    sub.add_parser("list-users")
    sub.add_parser("status")

    check_parser = sub.add_parser("check-user")
    check_parser.add_argument("slug")

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
    elif args.cmd == "links":
        print_links()
    elif args.cmd == "list-users":
        list_users()
    elif args.cmd == "status":
        status()
    elif args.cmd == "check-user":
        check_user(args.slug)
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
