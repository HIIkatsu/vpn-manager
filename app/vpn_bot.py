#!/usr/bin/env python3
import json, os, re, secrets, subprocess, time, unicodedata, urllib.parse, urllib.request
from pathlib import Path

VPN_MANAGER_HOME = Path(os.environ.get('VPN_MANAGER_HOME', '/root/vpn-manager'))
BASE = VPN_MANAGER_HOME
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



def _latin_alias(value):
    table = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    }
    out=[]
    for ch in str(value).lower():
        out.append(table.get(ch,ch))
    alias=''.join(out)
    alias=unicodedata.normalize('NFKD', alias).encode('ascii','ignore').decode('ascii')
    alias=re.sub(r'[^a-z0-9]+','',alias)
    return alias


def _resolve_users(query):
    users=json.loads((BASE/'users.json').read_text(encoding='utf-8')).get('users',[])
    q=str(query).strip()
    if not q:
        return [], users
    q_l=q.lower()
    q_alias=_latin_alias(q)
    matches=[]
    for u in users:
        slug=str(u.get('slug','')).strip()
        name=str(u.get('name','')).strip()
        candidates={slug.lower(), name.lower(), _latin_alias(name), _latin_alias(slug)}
        if q_l in candidates or (q_alias and q_alias in candidates):
            matches.append(u)
    return matches, users


def _format_user(u):
    name=str(u.get('name','')).strip()
    slug=str(u.get('slug','')).strip()
    return f"{name} ({slug})" if name else slug
def handle(text):
    parts=text.strip().split()
    cmd=parts[0] if parts else ''
    arg=parts[1] if len(parts)>1 else ''
    if cmd=='/start': return 'VPN admin bot online.'
    if cmd=='/status':
        c,o,e=run(['python3','/root/vpn-manager/app/vpn_health.py'])
        return (o+'\n'+e).strip()[:3500]
    if cmd=='/users':
        users=json.loads((BASE/'users.json').read_text(encoding='utf-8')).get('users',[])
        lines=[]
        for x in users:
            slug=str(x.get('slug','')).strip()
            if not slug:
                continue
            s=user_stats(slug)
            lines.append(f"{_format_user(x)}: open={s['opened']} login={s['logged_in']} copied={s['copied']}")
        return '\n'.join(lines) or 'No users'
    if cmd=='/invite' and arg:
        settings=json.loads((BASE/'settings.json').read_text(encoding='utf-8'))
        matches,_=_resolve_users(arg)
        if not matches:
            return 'User not found'
        if len(matches)>1:
            return 'Multiple matches:\n' + '\n'.join(f'- {_format_user(u)}' for u in matches) + '\nPlease be more specific.'
        user=matches[0]
        path=str(settings.get('public_user_path','')).strip('/')
        if not path:
            path='vpn-user-vpn'
        return f"https://{settings['domain']}/{path}/?invite=1\nslug={user['slug']}"
    if cmd=='/check' and arg:
        matches,_=_resolve_users(arg)
        if not matches:
            return 'User not found'
        if len(matches)>1:
            return 'Multiple matches:\n' + '\n'.join(f'- {_format_user(u)}' for u in matches) + '\nPlease be more specific.'
        c,o,e=run(['vpn-manager','check-user',matches[0]['slug']]); return (o+'\n'+e)[:3500]
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
