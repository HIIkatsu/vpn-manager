#!/usr/bin/env python3
import html
import json
import secrets
import subprocess
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path('/root/vpn-manager')
CFG = BASE / 'bot_config.json'
EVENTS = BASE / 'invite_events.json'
PENDING = {}
MAX_TG_MESSAGE = 3800


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
    admin_icon = '🔒' if admin.get('code') == 401 else icon(admin.get('ok'))
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
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_health.py','--json'])
        if c not in (0, 1):
            raise RuntimeError((o+'\n'+e).strip() or 'status command failed')
        return format_status(json.loads(o))
    if cmd=='/users':
        u=json.loads((BASE/'users.json').read_text())['users']
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
        users = {u['slug']: u for u in json.loads((BASE/'users.json').read_text())['users']}
        u = users.get(arg)
        if not u:
            return f"❌ <b>Error</b>\n<code>user not found: {esc(arg)}</code>"
        settings = json.loads((BASE/'settings.json').read_text())
        page = f"https://{settings['domain']}/{u.get('path', '').strip('/')}/?invite=1"
        code = esc(u.get('access_code', 'n/a'))
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
        c,o,e=run(['vpn-manager','check-user',arg])
        status = '✅ User profile looks OK' if c == 0 else '❌ User profile has issues'
        return f"🔍 <b>Check</b> <code>{esc(arg)}</code>\n\n<pre>{esc((o + chr(10) + e).strip()[:2500] or 'no output')}</pre>\n\n<b>Result</b>\n{status}"
    if cmd=='/backup':
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_backup.py'])
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
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_health.py'])
        return 'Repair done:\n'+(o+'\n'+e)[:3000]
    if cmd=='/reissue' and arg:
        token=secrets.token_hex(4)
        PENDING[token]=arg
        return f'Confirm reissue for {arg}: /confirm {token}'
    if cmd=='/confirm' and arg:
        slug=PENDING.get(arg)
        if not slug: return 'Bad/expired token'
        c,o,e=run(['vpn-manager','reissue-user',slug])
        PENDING.pop(arg,None)
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
                for chunk in split_for_telegram(resp):
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
