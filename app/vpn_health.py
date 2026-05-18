#!/usr/bin/env python3
import argparse
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

VPN_MANAGER_HOME = Path(os.environ.get('VPN_MANAGER_HOME', '/root/vpn-manager'))
BASE = VPN_MANAGER_HOME
SETTINGS = BASE / 'settings.json'
USERS = BASE / 'users.json'
XRAY_CONFIG = Path('/usr/local/etc/xray/config.json')
ROUTING_JSON = Path('/var/www/vpn/routing.json')

SERVICES = ['xray', 'nginx', 'vpn-admin', 'vpn-user']
PORTS = [443, 8443, 10085, 8010, 8011]


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def is_port_listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', int(port))) == 0


def service_status(name):
    p = subprocess.run(['systemctl', 'is-active', name], text=True, capture_output=True)
    return (p.stdout or '').strip() == 'active', (p.stdout or p.stderr).strip()


def http_ok(url, allow_statuses=(200,)):
    allowed = {int(x) for x in allow_statuses}
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.status in allowed, f'HTTP {r.status}'
    except urllib.error.HTTPError as e:
        return e.code in allowed, f'HTTP {e.code}'
    except Exception as e:
        return False, str(e)


def user_path(settings):
    sub = str(settings.get('subscription_path', 'vpn')).strip('/')
    if sub.startswith('vpn-'):
        return 'vpn-user-' + sub.split('vpn-', 1)[1]
    return sub + '-user'


def run_check():
    settings = load_json(SETTINGS)
    users_doc = load_json(USERS)
    subdir = Path(settings['subscription_dir'])
    domain = settings['domain']
    routing_path = subdir / 'routing.json'

    report = {'ok': True, 'checks': {}}

    services = {}
    for s in SERVICES:
        ok, msg = service_status(s)
        services[s] = {'ok': ok, 'status': msg}
    report['checks']['services'] = services

    ports = {str(p): {'ok': is_port_listening(p)} for p in PORTS}
    report['checks']['ports'] = ports

    admin_ok, admin_msg = http_ok(f'https://{domain}/vpn-admin/', allow_statuses=(200, 401, 403))
    user_ok, user_msg = http_ok(f'https://{domain}/{user_path(settings)}/', allow_statuses=(200,))
    report['checks']['http'] = {'admin': {'ok': admin_ok, 'status': admin_msg}, 'user': {'ok': user_ok, 'status': user_msg}}

    route_ok = False
    route_msg = 'missing'
    if routing_path.exists():
        try:
            json.loads(routing_path.read_text(encoding='utf-8'))
            route_ok = True
            route_msg = 'valid json'
        except Exception as e:
            route_msg = f'invalid json: {e}'
    report['checks']['routing_json'] = {'ok': route_ok, 'status': route_msg, 'path': str(routing_path)}

    xray_clients = set()
    if XRAY_CONFIG.exists():
        try:
            cfg = load_json(XRAY_CONFIG)
            for inbound in cfg.get('inbounds', []):
                for c in inbound.get('settings', {}).get('clients', []):
                    email = str(c.get('email', ''))
                    if email:
                        xray_clients.add(email.split('@')[0])
        except Exception:
            pass

    users = {}
    for u in users_doc.get('users', []):
        slug = str(u.get('slug', '')).strip()
        if not slug or not u.get('enabled', True):
            continue
        txt = subdir / f'{slug}.txt'
        jsn = subdir / f'{slug}.json'
        users[slug] = {
            'txt_exists': txt.exists(),
            'json_exists': jsn.exists(),
            'xray_client_exists': slug in xray_clients,
            'ok': txt.exists() and jsn.exists() and slug in xray_clients,
        }
    report['checks']['users'] = users

    report['ok'] = all(v['ok'] for v in services.values()) and all(v['ok'] for v in ports.values()) and admin_ok and user_ok and route_ok and all(v['ok'] for v in users.values())
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    rep = run_check()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print('VPN health-check:', 'OK' if rep['ok'] else 'FAIL')
        for s, v in rep['checks']['services'].items():
            print(f'service {s}: {"OK" if v["ok"] else "FAIL"} ({v["status"]})')
        for p, v in rep['checks']['ports'].items():
            print(f'port {p}: {"LISTEN" if v["ok"] else "MISS"}')
        print('http admin:', rep['checks']['http']['admin']['status'])
        print('http user :', rep['checks']['http']['user']['status'])
        print('routing.json:', rep['checks']['routing_json']['status'])
        for slug, v in rep['checks']['users'].items():
            print(f'user {slug}: {"OK" if v["ok"] else "FAIL"} (txt={v["txt_exists"]}, json={v["json_exists"]}, xray={v["xray_client_exists"]})')
    raise SystemExit(0 if rep['ok'] else 1)

if __name__ == '__main__':
    main()
