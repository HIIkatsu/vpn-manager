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
USERS_PAGE_SIZE = 6


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


def load_users():
    return json.loads((BASE / 'users.json').read_text(encoding='utf-8')).get('users', [])


def find_user(query):
    matches, _ = _resolve_users(query)
    if len(matches) == 1:
        return matches[0]
    return None


def compact_check_text(user):
    slug = str(user.get('slug', '')).strip()
    name = str(user.get('name', '')).strip() or slug
    check_text = handle(f'/check {slug}')
    return f"🔎 <b>Check {html.escape(name)}</b>\n\n{check_text.splitlines()[2] if len(check_text.splitlines()) > 2 else ''}\n\n" + '\n'.join(check_text.splitlines()[3:])


def render_users_page(page=0):
    users = [u for u in load_users() if str(u.get('slug', '')).strip()]
    users.sort(key=lambda x: (str(x.get('name', '')).lower(), str(x.get('slug', '')).lower()))
    total = len(users)
    enabled = sum(1 for u in users if u.get('enabled', True))
    disabled = total - enabled
    pages = max(1, (total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * USERS_PAGE_SIZE
    visible = users[start:start + USERS_PAGE_SIZE]
    lines = ["👥 <b>Users</b>", f"total: <b>{total}</b> · enabled: <b>{enabled}</b> · disabled: <b>{disabled}</b>", ""]
    for idx, u in enumerate(visible, start=1):
        slug = str(u.get('slug', '')).strip()
        name = str(u.get('name', '')).strip() or slug
        stats = user_stats(slug)
        state = "🟢" if u.get('enabled', True) else "🔴"
        lines.append(f"{idx}. {state} <b>{html.escape(name)}</b> <code>{html.escape(slug)}</code>")
        lines.append(f"open/login/copied: <code>{stats['opened']}/{stats['logged_in']}/{stats['copied']}</code>")
    kb = []
    for u in visible:
        slug = str(u.get('slug', '')).strip()
        kb.append([{"text": str(u.get('name', '')).strip() or slug, "callback_data": f"u:d:{slug}"}])
    nav = []
    if page > 0:
        nav.append({"text": "◀️ Prev", "callback_data": f"u:p:{page - 1}"})
    nav.append({"text": "🔄 Refresh", "callback_data": f"u:p:{page}"})
    if page < pages - 1:
        nav.append({"text": "Next ▶️", "callback_data": f"u:p:{page + 1}"})
    kb.append(nav)
    return '\n'.join(lines).strip(), {"inline_keyboard": kb}


def render_user_detail(user, source_page=0):
    slug = str(user.get('slug', '')).strip()
    name = str(user.get('name', '')).strip() or slug
    s = user_stats(slug)
    state = "enabled" if user.get('enabled', True) else "disabled"
    check_text = handle(f'/check {slug}')
    lines = [
        f"👤 <b>{html.escape(name)}</b>",
        f"slug: <code>{html.escape(slug)}</code>",
        f"state: <b>{state}</b>",
        f"open/login/copied: <code>{s['opened']}/{s['logged_in']}/{s['copied']}</code>",
    ]
    client_line = next((line for line in check_text.splitlines() if 'client: <code>' in line), '')
    if client_line:
        lines += ["", "📌 <b>Client status</b>", client_line]
    action = "Disable" if user.get('enabled', True) else "Enable"
    toggle = "dis" if user.get('enabled', True) else "ena"
    kb = {"inline_keyboard": [
        [{"text": "Invite", "callback_data": f"u:i:{slug}:{source_page}"}, {"text": "Check", "callback_data": f"u:c:{slug}:{source_page}"}],
        [{"text": "Reissue code", "callback_data": f"u:r:{slug}:{source_page}"}],
        [{"text": action, "callback_data": f"u:t:{toggle}:{slug}:{source_page}"}],
        [{"text": "⬅️ Back", "callback_data": f"u:p:{source_page}"}],
    ]}
    return '\n'.join(lines), kb


def handle_callback(data):
    if not data.startswith('u:'):
        return "⚠️ Unknown action.", None
    parts = data.split(':')
    kind = parts[1]
    if kind == 'p':
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return render_users_page(page)
    if kind == 'd':
        user = find_user(parts[2] if len(parts) > 2 else '')
        if not user:
            return "⚠️ User not found or stale menu.", None
        return render_user_detail(user, 0)
    if kind in ('i', 'c', 'r'):
        slug = parts[2] if len(parts) > 2 else ''
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        user = find_user(slug)
        if not user:
            return "⚠️ User not found or stale menu.", None
        name = str(user.get('name', '')).strip() or slug
        if kind == 'i':
            return handle(f"/invite {slug}"), {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": f"u:d:{slug}"}], [{"text": "📋 Users", "callback_data": f"u:p:{page}"}]]}
        if kind == 'c':
            return compact_check_text(user), {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": f"u:d:{slug}"}], [{"text": "📋 Users", "callback_data": f"u:p:{page}"}]]}
        return f"⚠️ Confirm reissue for <b>{html.escape(name)}</b>?", {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": f"u:rc:{slug}:{page}"}, {"text": "Cancel", "callback_data": f"u:d:{slug}"}]]}
    if kind == 'rc':
        slug = parts[2] if len(parts) > 2 else ''
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        rc, out, err = run(['python3', str(BASE / 'app' / 'vpn-manager.py'), 'reissue-user', slug])
        body = out or err or 'No output'
        text = f"{'✅' if rc == 0 else '❌'} <b>Reissue {html.escape(slug)}</b>\n\n{safe_pre(body[:1800])}"
        return text, {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": f"u:d:{slug}"}], [{"text": "📋 Users", "callback_data": f"u:p:{page}"}]]}
    if kind == 't':
        action = parts[2] if len(parts) > 2 else ''
        slug = parts[3] if len(parts) > 3 else ''
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        verb = "disable" if action == "dis" else "enable"
        return f"⚠️ Confirm {verb} for <code>{html.escape(slug)}</code>?", {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": f"u:tc:{action}:{slug}:{page}"}, {"text": "Cancel", "callback_data": f"u:d:{slug}"}]]}
    if kind == 'tc':
        action = parts[2] if len(parts) > 2 else ''
        slug = parts[3] if len(parts) > 3 else ''
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        cmd = 'disable-user' if action == 'dis' else 'enable-user'
        rc, out, err = run(['python3', str(BASE / 'app' / 'vpn-manager.py'), cmd, slug])
        text = f"{'✅' if rc == 0 else '❌'} <b>{'Disabled' if action == 'dis' else 'Enabled'} {html.escape(slug)}</b>\n{safe_pre((out or err or 'No output')[:1200])}"
        return text, {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": f"u:d:{slug}"}], [{"text": "📋 Users", "callback_data": f"u:p:{page}"}]]}
    return "⚠️ Unknown action.", None


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
        return '❌ <b>VPN status</b>\n\n❌ <b>Result</b>\nUnable to get health-check output.'
    rep = json.loads(out)
    checks = rep.get('checks', {})
    services, ports = checks.get('services', {}), checks.get('ports', {})
    http_c, routing, users = checks.get('http', {}), checks.get('routing_json', {}), checks.get('users', {})
    lines = ['✅ <b>VPN status</b>', '', '⚙️ <b>Services</b>']
    for key, label in [('xray', 'Xray'), ('nginx', 'Nginx'), ('vpn-admin', 'Admin'), ('vpn-user', 'User page')]:
        ok = services.get(key, {}).get('ok', False)
        status = html.escape(services.get(key, {}).get('status', 'unknown'))
        lines.append(f"{'❌ ' if not ok else ''}{label}: <code>{status}</code>")
    lines += ['', '🔌 <b>Ports</b>']
    for port in ['443', '8443', '10085', '8010', '8011']:
        ok = ports.get(port, {}).get('ok', False)
        lines.append(f"{'❌ ' if not ok else ''}{port}: <code>{'LISTEN' if ok else 'NOT LISTEN'}</code>")
    admin_ok = http_c.get('admin', {}).get('ok', False)
    user_ok = http_c.get('user', {}).get('ok', False)
    user_status = html.escape(str(http_c.get('user', {}).get('status', 'error')).replace('HTTP ', ''))
    routing_ok = routing.get('ok', False)
    lines += ['', '🌐 <b>HTTP</b>', f"{'❌ ' if not admin_ok else ''}Admin: <code>{'protected, alive' if admin_ok else 'failed'}</code>", f"{'❌ ' if not user_ok else ''}User page: <code>{user_status}</code>", f"{'❌ ' if not routing_ok else ''}routing.json: <code>{'valid' if routing_ok else 'invalid'}</code>"]
    lines += ['', '👥 <b>Users</b>']
    for slug, info in users.items():
        ok = info.get('ok', False)
        lines.append(f"{'❌ ' if not ok else ''}{html.escape(slug)}: <code>{'OK' if ok else 'FAIL'}</code>")
    healthy = bool(rep.get('ok'))
    lines += ['', f"{'✅' if healthy else '❌'} <b>Result</b>", 'System looks healthy' if healthy else 'Issues found']
    return '\n'.join(lines)




def split_html_message(text, limit=3500):
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    current_len = 0
    for line in text.split('\n'):
        add = len(line) + (1 if current else 0)
        if current and current_len + add > limit:
            chunks.append('\n'.join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add
    if current:
        chunks.append('\n'.join(current))
    return chunks

def handle(text):
    parts = text.strip().split()
    cmd = parts[0] if parts else ''
    arg = ' '.join(parts[1:]) if len(parts) > 1 else ''
    if cmd == '/start':
        return '✅ <b>VPN admin bot online.</b>'
    if cmd == '/status':
        return format_status()
    if cmd == '/users':
        text, _ = render_users_page(0)
        return text
    if cmd == '/invite' and not arg:
        return 'Usage: /invite <name or slug>\nExample: /invite mama'
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
    if cmd == '/check' and not arg:
        return 'Usage: /check <name or slug>\nExample: /check alise'
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
            try:
                json.loads(routing.read_text(encoding='utf-8'))
            except Exception:
                routing_ok = False
        client_present = False
        xray_paths = [Path('/usr/local/etc/xray/config.json'), BASE / 'xray' / 'config.json']
        for xray_path in xray_paths:
            if not xray_path.exists():
                continue
            try:
                xcfg = json.loads(xray_path.read_text(encoding='utf-8'))
            except Exception:
                continue
            for inbound in xcfg.get('inbounds', []):
                for client in inbound.get('settings', {}).get('clients', []):
                    email = str(client.get('email', '')).strip()
                    if email and email.split('@')[0] == slug:
                        client_present = True
                        break
                if client_present:
                    break
            if client_present:
                break

        required_ok = txt and js and routing_ok and client_present
        return '\n'.join([
            f"🔍 <b>Check {html.escape(name)}</b>",
            f"<code>{html.escape(slug)}</code>",
            '',
            '📁 <b>Required files</b>',
            'users.json: <code>found</code>',
            f"{'❌ ' if not txt else ''}subscription txt: <code>{'exists' if txt else 'missing'}</code>",
            f"{'❌ ' if not js else ''}subscription json: <code>{'exists' if js else 'missing'}</code>",
            f"{'❌ ' if not routing_ok else ''}routing.json: <code>{'exists, valid' if routing_ok else 'missing/invalid'}</code>",
            '',
            '🧩 <b>Xray</b>',
            f"{'❌ ' if not client_present else ''}client: <code>{'present' if client_present else 'missing'}</code>",
            '',
            '🟡 <b>Optional fallback files</b>',
            f"443 txt/json: <code>{'exists' if p443 else 'missing optional'}</code>",
            f"8443 txt/json: <code>{'exists' if p8443 else 'missing optional'}</code>",
            '',
            f"{'✅' if required_ok else '❌'} <b>Result</b>",
            'User profile looks OK' if required_ok else 'Required items are missing',
        ])
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
                cb = u.get('callback_query', {})
                if cb:
                    cq_id = cb.get('id')
                    msg = cb.get('message', {})
                    chat_id = msg.get('chat', {}).get('id')
                    data = cb.get('data', '')
                    if not chat_id:
                        continue
                    if not allowed(chat_id, cfg):
                        api(token, 'answerCallbackQuery', {'callback_query_id': cq_id, 'text': 'Access denied'})
                        continue
                    try:
                        text, kb = handle_callback(data)
                        payload = {'chat_id': chat_id, 'message_id': msg.get('message_id'), 'text': text, 'parse_mode': 'HTML'}
                        if kb:
                            payload['reply_markup'] = json.dumps(kb)
                        api(token, 'editMessageText', payload)
                        api(token, 'answerCallbackQuery', {'callback_query_id': cq_id})
                    except Exception:
                        api(token, 'answerCallbackQuery', {'callback_query_id': cq_id, 'text': 'Action failed'})
                    continue
                chat_id = m.get('chat', {}).get('id')
                text = m.get('text', '')
                if not chat_id or not text:
                    continue
                if not allowed(chat_id, cfg):
                    api(token, 'sendMessage', {'chat_id': chat_id, 'text': 'Access denied', 'parse_mode': 'HTML'})
                    continue
                if text.strip().startswith('/users'):
                    msg_text, kb = render_users_page(0)
                    api(token, 'sendMessage', {'chat_id': chat_id, 'text': msg_text, 'parse_mode': 'HTML', 'reply_markup': json.dumps(kb)})
                    continue
                for chunk in split_html_message(handle(text)):
                    api(token, 'sendMessage', {'chat_id': chat_id, 'text': chunk, 'parse_mode': 'HTML'})
        except Exception:
            traceback.print_exc()
            time.sleep(2)


if __name__ == '__main__':
    main()
