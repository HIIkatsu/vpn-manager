#!/usr/bin/env python3
import json, secrets, subprocess, time, urllib.parse, urllib.request
from pathlib import Path

BASE = Path('/root/vpn-manager')
CFG = BASE / 'bot_config.json'
EVENTS = BASE / 'invite_events.json'
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
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_health.py'])
        return (o+'\n'+e).strip()[:3500]
    if cmd=='/users':
        u=json.loads((BASE/'users.json').read_text())['users']
        lines=[]
        for x in u:
            s=user_stats(x['slug'])
            lines.append(f"{x['slug']}: open={s['opened']} login={s['logged_in']} copied={s['copied']}")
        return '\n'.join(lines) or 'No users'
    if cmd=='/invite' and arg:
        return f"https://{json.loads((BASE/'settings.json').read_text())['domain']}/vpn-user-vpn/?invite=1\nslug={arg}"
    if cmd=='/check' and arg:
        c,o,e=run(['vpn-manager','check-user',arg]); return (o+'\n'+e)[:3500]
    if cmd=='/backup':
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_backup.py']); return (o+'\n'+e)[:3500]
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
                    api(token,'sendMessage',{'chat_id':chat_id,'text':'Access denied'})
                    continue
                resp=handle(text)
                api(token,'sendMessage',{'chat_id':chat_id,'text':resp})
        except Exception:
            time.sleep(2)

if __name__=='__main__':
    main()
