#!/usr/bin/env python3
import argparse, os, tarfile
from datetime import datetime, timedelta
from pathlib import Path

VPN_MANAGER_HOME = Path(os.environ.get('VPN_MANAGER_HOME', '/root/vpn-manager'))
BASE = VPN_MANAGER_HOME
BACKUP_DIR = BASE / 'backups' / 'daily'
KEEP_DAYS = 14
PROTECT_MARKERS = ('STABLE', 'manual', 'before-', 'codex')

FILES = [
    BASE / 'vpn_manager.py', BASE / 'vpn_admin.py', BASE / 'vpn_user.py',
    BASE / 'settings.json', BASE / 'routes.json', BASE / 'users.json', BASE / 'user_access.json',
    BASE / 'auth.json', Path('/usr/local/etc/xray/config.json'),
    Path('/etc/nginx/nginx.conf'), Path('/etc/nginx/snippets/vpn-subscriptions.conf'), Path('/etc/nginx/snippets/vpn-user-pages.conf')
]


def create_backup(dry_run=False):
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f'{ts}.tar.gz'
    included = [p for p in FILES if p.exists()]
    if dry_run:
        print('Would create:', target)
        for p in included:
            print(' +', p)
        return
    with tarfile.open(target, 'w:gz') as tar:
        for p in included:
            tar.add(p, arcname=str(p).lstrip('/'))
    print('Created backup:', target)


def prune(dry_run=False):
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    if not BACKUP_DIR.exists():
        return
    for p in BACKUP_DIR.iterdir():
        if any(m in p.name for m in PROTECT_MARKERS):
            continue
        if p.is_file() and p.suffixes[-2:] == ['.tar', '.gz']:
            dt = datetime.fromtimestamp(p.stat().st_mtime)
            if dt < cutoff:
                if dry_run:
                    print('Would delete:', p)
                else:
                    p.unlink(missing_ok=True)
                    print('Deleted:', p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    create_backup(dry_run=args.dry_run)
    prune(dry_run=args.dry_run)

if __name__ == '__main__':
    main()
