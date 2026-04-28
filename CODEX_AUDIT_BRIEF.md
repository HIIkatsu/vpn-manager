# VPN Manager audit brief

This repository snapshot reflects the current server code, but real secrets are not committed.

Real server paths:
- /root/vpn-manager/vpn_manager.py
- /root/vpn-manager/vpn_admin.py
- /root/vpn-manager/vpn_user.py
- /usr/local/bin/vpn-manager -> /root/vpn-manager/vpn_manager.py

Do not change code immediately. First produce an audit and a proposed plan.

Important behavior:
- add/toggle/delete in admin should create pending changes
- Xray should be updated only after "Apply changes"
- JSON subscription/config is important because it carries routing rules
- raw vless:// is fallback only
- routes.json handles direct routing for RU domains/IPs
- do not overwrite UX style
- do not commit secrets
