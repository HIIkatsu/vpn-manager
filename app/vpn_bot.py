#!/usr/bin/env python3
import html
import json
import os
import re
import secrets
import subprocess
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

VPN_MANAGER_HOME = Path(os.environ.get("VPN_MANAGER_HOME", "/root/vpn-manager"))
BASE = VPN_MANAGER_HOME
CFG = BASE / 'bot_config.json'
EVENTS = BASE / 'invite_events.json'
PENDING = BASE / 'admin_pending_changes.json'
MAX_TG_MESSAGE = 3800


def load_cfg():
    return json.loads(CFG.read_text(encoding='utf-8'))


def load_pending():
    if PENDING.exists():
        return json.loads(PENDING.read_text(encoding='utf-8'))
    return {}


def save_pending(data):
    PENDING.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def api(token, method, data=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def esc(value):
    return html.escape(str(value), quote=False)


def icon(ok):
    return '✅' if ok else '❌'


def split_for_telegram(text, limit=MAX_TG_MESSAGE):
    if len(text) <= limit:
        return [text]
    parts, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind('\n', 0, limit)
        if cut < 1:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip('\n')
    if rest:
        parts.append(rest)
    return parts


def split_html_sections(text, limit=MAX_TG_MESSAGE):
    sections = text.split('\n\n')
    chunks = []
    current = ''
    for sec in sections:
        candidate = sec if not current else current + '\n\n' + sec
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ''
        if len(sec) <= limit:
            current = sec
        else:
            chunks.extend(split_for_telegram(sec, limit))
    if current:
        chunks.append(current)
    return chunks or ['']


def public_user_path(settings):
    sub = str(settings.get('subscription_path', 'vpn')).strip('/')
    if sub.startswith('vpn-'):
        return 'vpn-user-' + sub.split('vpn-', 1)[1]
    return sub + '-user'


def load_users():
    return json.loads((BASE / 'users.json').read_text(encoding='utf-8')).get('users', [])


def find_user(query):
    def normalize(v):
        v = unicodedata.normalize('NFKD', str(v or '')).casefold()
        v = ''.join(ch for ch in v if not unicodedata.combining(ch))
        return re.sub(r'[^a-z0-9]+', '', v)
    q = str(query or '').strip()
    if not q:
        return None, []
    ql = q.casefold()
    qn = normalize(q)
    matches = []
    for u in load_users():
        vals = [u.get('slug', ''), u.get('name', ''), u.get('alias', ''), u.get('username', '')]
        norm_vals = [normalize(x) for x in vals if x]
        raw_vals = [str(x).strip().casefold() for x in vals if x]
        if ql in raw_vals or (qn and qn in norm_vals):
            matches.append(u)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def access_codes():
    path = BASE / 'user_access.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return data.get('codes', data if isinstance(data, dict) else {})


def format_status(report):
    lines = [f"{icon(report.get('ok'))} <b>VPN status</b>", "", "<b>Services</b>"]
    name_map = {'xray': 'Xray', 'nginx': 'Nginx', 'vpn-admin': 'Admin', 'vpn-user': 'User page'}
    for key in ['xray', 'nginx', 'vpn-admin', 'vpn-user']:
        item = report['checks']['services'].get(key, {})
        lines.append(f"{icon(item.get('ok'))} {name_map[key]}: {esc(item.get('status', 'unknown'))}")

    lines.extend(["", "<b>Ports</b>"])
    for p in ['443', '8443', '10085', '8010', '8011']:
        ok = report['checks']['ports'].get(p, {}).get('ok', False)
        lines.append(f"{icon(ok)} {p}")

    lines.extend(["", "<b>HTTP</b>"])
    admin = report['checks']['http']['admin']
    user = report['checks']['http']['user']
    admin_icon = '🔒' if admin.get('code') in (401, 403) else icon(admin.get('ok'))
    lines.append(f"{admin_icon} Admin: {esc(admin.get('summary', admin.get('status', 'unknown')))}")
    lines.append(f"{icon(user.get('ok'))} User page: {esc(user.get('summary', user.get('status', 'unknown')))}")
    route = report['checks']['routing_json']
    lines.append(f"{icon(route.get('ok'))} routing.json: {esc(route.get('status', 'unknown'))}")

    lines.extend(["", "<b>Users</b>"])
    users = list(report['checks'].get('users', {}).items())
    max_users = 25
    for slug, info in users[:max_users]:
        fragments = []
        if info.get('txt_exists'):
            fragments.append('txt')
        if info.get('json_exists'):
            fragments.append('json')
        if info.get('xray_client_exists'):
            fragments.append('xray')
        state = '/'.join(fragments) + (' OK' if info.get('ok') else ' issue')
        label = esc(info.get('name') or slug)
        lines.append(f"{icon(info.get('ok'))} {label}: {state}")
    if len(users) > max_users:
        lines.append(f"…and {len(users)-max_users} more")

    lines.extend(["", "<b>Result</b>", f"{icon(report.get('ok'))} {'System looks healthy' if report.get('ok') else 'System needs attention'}"])
    return '\n'.join(lines)

def allowed(chat_id, cfg):
    return int(chat_id) in [int(x) for x in cfg.get('admin_ids', [])]

def user_stats(slug):
    data={'opened':0,'logged_in':0,'copied':0,'last':{}}
    if EVENTS.exists():
        ev=json.loads(EVENTS.read_text(encoding='utf-8')).get('events',[])
        for e in ev:
            if e.get('slug')!=slug: continue
            et=e.get('event')
            if et=='page_opened': data['opened']+=1
            if et=='login_success': data['logged_in']+=1
            if et=='profile_copied': data['copied']+=1
            data['last'][et]=e.get('ts')
    return data

def handle(text):
    parts=text.strip().split()
    cmd=parts[0] if parts else ''
    arg=parts[1] if len(parts)>1 else ''
    if cmd=='/start': return 'VPN admin bot online.'
    if cmd=='/status':
        c,o,e=run(['python3', str(BASE / 'app' / 'vpn_health.py'),'--json'])
        if c not in (0, 1):
            raise RuntimeError((o+'\n'+e).strip() or 'status command failed')
        return format_status(json.loads(o))
    if cmd=='/users':
        u=load_users()
        lines=['👥 <b>Users</b>', '']
        for x in u:
            s=user_stats(x['slug'])
            name = esc(x.get('name') or x['slug'])
            slug = esc(x['slug'])
            lines.append(f"✅ {name} <code>{slug}</code>")
            lines.append(f"open: {s['opened']} · login: {s['logged_in']} · copied: {s['copied']}")
            lines.append('')
        return '\n'.join(lines).strip() or 'No users'
    if cmd=='/invite' and arg:
        u, matches = find_user(arg)
        if matches:
            opts = '\n'.join(f"• {esc(x.get('name') or x.get('slug'))} <code>{esc(x.get('slug'))}</code>" for x in matches[:10])
            return f"⚠️ <b>Multiple users matched</b>\n{opts}"
        if not u:
            return f"❌ <b>Error</b>\n<code>user not found: {esc(arg)}</code>"
        settings = json.loads((BASE/'settings.json').read_text())
        page = f"https://{settings['domain']}/{public_user_path(settings)}/?invite=1"
        code = esc(access_codes().get(u.get('slug'), 'n/a'))
        name = esc(u.get('name') or u.get('slug'))
        lines = [
            f"🔗 <b>Invite for {name}</b>",
            "",
            "Страница:",
            f"<code>{esc(page)}</code>",
            "",
            "Код:",
            f"<code>{code}</code>",
            "",
            "Готовый текст:",
            f"<pre>🔐 VPN доступ — {name}\n\nСтраница подключения:\n{esc(page)}\n\nКод доступа: {code}</pre>",
        ]
        return '\n'.join(lines)
    if cmd=='/check' and arg:
        u, matches = find_user(arg)
        if matches:
            opts = '\n'.join(f"• {esc(x.get('name') or x.get('slug'))} <code>{esc(x.get('slug'))}</code>" for x in matches[:10])
            return f"⚠️ <b>Multiple users matched</b>\n{opts}"
        slug = u.get('slug', arg) if u else arg
        c,o,e=run(['vpn-manager','check-user',slug])
        status = '✅ User profile looks OK' if c == 0 else '❌ User profile has issues'
        title = f"🔍 <b>Check {esc(u.get('name') or slug)}</b> <code>{esc(slug)}</code>" if u else f"🔍 <b>Check</b> <code>{esc(slug)}</code>"
        return f"{title}\n\n<pre>{esc((o + chr(10) + e).strip()[:2500] or 'no output')}</pre>\n\n<b>Result</b>\n{status}"
    if cmd=='/backup':
        c,o,e=run(['python3', str(BASE / 'app' / 'vpn_backup.py')])
        if c != 0:
            raise RuntimeError((o+'\n'+e).strip() or 'backup failed')
        backup_path = (o+'\n'+e).strip().splitlines()[-1]
        return f"📦 <b>Backup created</b>\n\n<code>{esc(backup_path)}</code>"
    if cmd=='/repair':
        c,o,e=run(['vpn-manager','apply','--dry-run'])
        if c!=0: return 'Dry-run failed:\n'+(o+'\n'+e)[:3000]
        c,o,e=run(['vpn-manager','apply'])
        if c!=0: return 'Apply failed:\n'+(o+'\n'+e)[:3000]
        run(['systemctl','restart','vpn-admin'])
        run(['systemctl','restart','vpn-user'])
        c,o,e=run(['python3', str(BASE / 'app' / 'vpn_health.py')])
        return 'Repair done:\n'+(o+'\n'+e)[:3000]
    if cmd=='/reissue' and arg:
        u, matches = find_user(arg)
        if matches:
            return f"⚠️ <b>Multiple users matched</b>"
        if u:
            arg = u.get('slug', arg)
        token=secrets.token_hex(4)
        pending = load_pending()
        pending[token] = arg
        save_pending(pending)
        return f'Confirm reissue for {arg}: /confirm {token}'
    if cmd=='/confirm' and arg:
        pending = load_pending()
        slug=pending.get(arg)
        if not slug: return 'Bad/expired token'
        c,o,e=run(['vpn-manager','reissue-user',slug])
        pending.pop(arg,None)
        save_pending(pending)
        return (o+'\n'+e)[:3500]
    return 'Unknown command'

def main():
    cfg=load_cfg(); token=cfg['bot_token']; offset=0
    while True:
        try:
            ups=api(token,'getUpdates',{'timeout':30,'offset':offset}).get('result',[])
            for u in ups:
                offset=u['update_id']+1
                m=u.get('message',{})
                chat_id=m.get('chat',{}).get('id')
                text=m.get('text','')
                if not chat_id or not text:
                    continue
                if not allowed(chat_id,cfg):
                    api(token,'sendMessage',{'chat_id':chat_id,'text':'❌ <b>Error</b>\n<code>access denied</code>','parse_mode':'HTML'})
                    continue
                resp=handle(text)
                for chunk in split_html_sections(resp):
                    api(token,'sendMessage',{'chat_id':chat_id,'text':chunk,'parse_mode':'HTML'})
        except Exception as e:
            short = esc(str(e) or e.__class__.__name__)
            traceback.print_exc()
            try:
                api(token,'sendMessage',{'chat_id':chat_id,'text':f'❌ <b>Error</b>\n<code>{short[:800]}</code>','parse_mode':'HTML'})
            except Exception:
                pass
            time.sleep(2)

if __name__=='__main__':
    main()
