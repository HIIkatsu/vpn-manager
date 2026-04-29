#!/usr/bin/env python3
import html
import json, os, re, secrets, subprocess, time, traceback, unicodedata, urllib.parse, urllib.request
from pathlib import Path

VPN_MANAGER_HOME = Path(os.environ.get('VPN_MANAGER_HOME', '/root/vpn-manager'))
BASE = VPN_MANAGER_HOME
CFG = BASE / 'bot_config.json'
EVENTS = BASE / 'invite_events.json'
ACCESS = BASE / 'user_access.json'
PENDING = {}


def load_cfg():
    return json.loads(CFG.read_text(encoding='utf-8'))


def api(token, method, data=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def allowed(chat_id, cfg):
    return int(chat_id) in [int(x) for x in cfg.get('admin_ids', [])]


def user_stats(slug):
    data = {'opened': 0, 'logged_in': 0, 'copied': 0, 'last': {}}
    if EVENTS.exists():
        ev = json.loads(EVENTS.read_text(encoding='utf-8')).get('events', [])
        for e in ev:
            if e.get('slug') != slug:
                continue
            et = e.get('event')
            if et == 'page_opened':
                data['opened'] += 1
            if et == 'login_success':
                data['logged_in'] += 1
            if et == 'profile_copied':
                data['copied'] += 1
            data['last'][et] = e.get('ts')
    return data


def _latin_alias(value):
    table = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'}
    out = []
    for ch in str(value).lower():
        out.append(table.get(ch, ch))
    alias = ''.join(out)
    alias = unicodedata.normalize('NFKD', alias).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', alias)


def _resolve_users(query):
    users = json.loads((BASE / 'users.json').read_text(encoding='utf-8')).get('users', [])
    q = str(query).strip()
    if not q:
        return [], users
    q_l = q.lower()
    q_alias = _latin_alias(q)
    matches = []
    for u in users:
        slug = str(u.get('slug', '')).strip()
        name = str(u.get('name', '')).strip()
        candidates = {slug.lower(), name.lower(), _latin_alias(name), _latin_alias(slug)}
        if q_l in candidates or (q_alias and q_alias in candidates):
            matches.append(u)
    return matches, users


def _format_user(u):
    name = str(u.get('name', '')).strip()
    slug = str(u.get('slug', '')).strip()
    return f"{name} ({slug})" if name else slug


def public_user_path(settings):
    sub = str(settings.get('subscription_path', 'vpn')).strip('/')
    if sub.startswith('vpn-'):
        return 'vpn-user-' + sub.split('vpn-', 1)[1]
    return sub + '-user'


def load_access_codes():
    if not ACCESS.exists():
        return {}
    data = json.loads(ACCESS.read_text(encoding='utf-8'))
    return data if isinstance(data, dict) else {}


def safe_pre(text):
    return f"<pre>{html.escape(str(text), quote=True)}</pre>"


def format_status():
    rc, out, _ = run(['python3', str(BASE / 'app' / 'vpn_health.py'), '--json'])
    if rc != 0 and not out:
        return '❌ <b>VPN status</b>\n\nНе удалось получить health-check.'
    rep = json.loads(out)
    checks = rep.get('checks', {})
    services, ports = checks.get('services', {}), checks.get('ports', {})
    http_c, routing, users = checks.get('http', {}), checks.get('routing_json', {}), checks.get('users', {})
    lines = ['✅ <b>VPN status</b>', '', '<b>Services</b>']
    for key, label in [('xray', 'Xray'), ('nginx', 'Nginx'), ('vpn-admin', 'Admin'), ('vpn-user', 'User page')]:
        ok = services.get(key, {}).get('ok', False)
        status = services.get(key, {}).get('status', 'unknown')
        lines.append(f"{'✅' if ok else '❌'} {label}: {html.escape(status)}")
    lines += ['', '<b>Ports</b>']
    for p in ['443', '8443', '10085', '8010', '8011']:
        lines.append(f"{'✅' if ports.get(p, {}).get('ok', False) else '❌'} {p}")
    admin_ok = http_c.get('admin', {}).get('ok', False)
    user_status = http_c.get('user', {}).get('status', 'error').replace('HTTP ', '')
    lines += ['', '<b>HTTP</b>', '🔒 Admin: protected, alive' if admin_ok else '❌ Admin: failed', f"{'✅' if http_c.get('user', {}).get('ok', False) else '❌'} User page: {html.escape(user_status)}", f"{'✅' if routing.get('ok', False) else '❌'} routing.json: {'valid' if routing.get('ok', False) else 'invalid'}"]
    lines += ['', '<b>Users</b>']
    for slug, info in users.items():
        ok = info.get('ok', False)
        lines.append(f"{'✅' if ok else '❌'} {html.escape(slug)}: txt/json/xray {'OK' if ok else 'FAIL'}")
    lines += ['', '<b>Result</b>', '✅ System looks healthy' if rep.get('ok') else '❌ Issues found']
    return '\n'.join(lines)


def handle(text):
    parts = text.strip().split()
    cmd = parts[0] if parts else ''
    arg = ' '.join(parts[1:]) if len(parts) > 1 else ''
    if cmd == '/start':
        return '✅ <b>VPN admin bot online.</b>'
    if cmd == '/status':
        return format_status()
    if cmd == '/users':
        users = json.loads((BASE / 'users.json').read_text(encoding='utf-8')).get('users', [])
        lines = ['👥 <b>Users</b>', '']
        for x in users:
            slug = str(x.get('slug', '')).strip()
            if not slug:
                continue
            name = str(x.get('name', '')).strip() or slug
            s = user_stats(slug)
            lines.append(f"✅ {html.escape(name)} <code>{html.escape(slug)}</code>")
            lines.append(f"open: {s['opened']} · login: {s['logged_in']} · copied: {s['copied']}")
            lines.append('')
        return '\n'.join(lines).strip()
    if cmd == '/invite' and arg:
        settings = json.loads((BASE / 'settings.json').read_text(encoding='utf-8'))
        matches, _ = _resolve_users(arg)
        if not matches:
            return '❌ User not found'
        if len(matches) > 1:
            return '❌ Multiple matches:\n' + '\n'.join(f'• {html.escape(_format_user(u))}' for u in matches)
        user = matches[0]
        slug = str(user.get('slug', '')).strip()
        code = str(load_access_codes().get(slug, '')).strip().upper() or 'N/A'
        url = f"https://{settings['domain']}/{public_user_path(settings)}/?invite=1"
        name = str(user.get('name', '')).strip() or slug
        pre = f"🔐 VPN доступ — {name}\n\nСтраница подключения:\n{url}\n\nКод доступа: {code}"
        return f"🔗 <b>Invite for {html.escape(name)}</b>\n\nСтраница:\n<code>{html.escape(url)}</code>\n\nКод:\n<code>{html.escape(code)}</code>\n\n{safe_pre(pre)}"
    if cmd == '/check' and arg:
        matches, _ = _resolve_users(arg)
        if not matches:
            return '❌ User not found'
        if len(matches) > 1:
            return '❌ Multiple matches:\n' + '\n'.join(f'• {html.escape(_format_user(u))}' for u in matches)
        u = matches[0]
        slug = str(u.get('slug', '')).strip()
        name = str(u.get('name', '')).strip() or slug
        settings = json.loads((BASE / 'settings.json').read_text(encoding='utf-8'))
        subdir = Path(settings['subscription_dir'])
        txt, js = (subdir / f'{slug}.txt').exists(), (subdir / f'{slug}.json').exists()
        p443 = (subdir / f'{slug}_443.txt').exists() and (subdir / f'{slug}_443.json').exists()
        p8443 = (subdir / f'{slug}_8443.txt').exists() and (subdir / f'{slug}_8443.json').exists()
        routing = subdir / 'routing.json'
        routing_ok = routing.exists()
        if routing_ok:
            try: json.loads(routing.read_text(encoding='utf-8'))
            except Exception: routing_ok = False
        return '\n'.join([f"🔍 <b>Check {html.escape(name)}</b> <code>{html.escape(slug)}</code>", '', '✅ users.json: found', f"{'✅' if txt else '❌'} subscription txt: {'exists' if txt else 'missing'}", f"{'✅' if js else '❌'} subscription json: {'exists' if js else 'missing'}", f"{'✅' if p443 else '❌'} 443 txt/json: {'exists' if p443 else 'missing'}", f"{'✅' if p8443 else '❌'} 8443 txt/json: {'exists' if p8443 else 'missing'}", f"{'✅' if routing_ok else '❌'} routing.json: {'exists, valid' if routing_ok else 'missing/invalid'}"])
    if cmd == '/backup':
        c, o, e = run(['python3', str(BASE / 'app' / 'vpn_backup.py')])
        body = o if o else e
        return ('✅ Backup done\n' if c == 0 else '❌ Backup failed\n') + safe_pre(body[:2500])
    return '❓ Unknown command'


def main():
    cfg = load_cfg(); token = cfg['bot_token']; offset = 0
    while True:
        try:
            ups = api(token, 'getUpdates', {'timeout': 30, 'offset': offset}).get('result', [])
            for u in ups:
                offset = u['update_id'] + 1
                m = u.get('message', {})
                chat_id = m.get('chat', {}).get('id')
                text = m.get('text', '')
                if not chat_id or not text:
                    continue
                if not allowed(chat_id, cfg):
                    api(token, 'sendMessage', {'chat_id': chat_id, 'text': 'Access denied', 'parse_mode': 'HTML'})
                    continue
                api(token, 'sendMessage', {'chat_id': chat_id, 'text': handle(text), 'parse_mode': 'HTML'})
        except Exception:
            traceback.print_exc()
            time.sleep(2)


if __name__ == '__main__':
    main()
