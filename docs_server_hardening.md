# Server hardening checklist (manual, safe)

These commands are intentionally **manual** and not auto-enforced in runtime code.

## File permissions for secrets

```bash
sudo chmod 600 /root/vpn-manager/settings.json
sudo chmod 600 /root/vpn-manager/users.json
sudo chmod 600 /root/vpn-manager/user_access.json
sudo chmod 600 /root/vpn-manager/auth.json
sudo chmod 600 /root/vpn-manager/admin_password*.txt
```

## Verify permissions

```bash
sudo stat -c '%a %n' \
  /root/vpn-manager/settings.json \
  /root/vpn-manager/users.json \
  /root/vpn-manager/user_access.json \
  /root/vpn-manager/auth.json \
  /root/vpn-manager/admin_password*.txt
```
