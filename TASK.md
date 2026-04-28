# VPN Manager cleanup task

Need to stabilize and polish a self-written VPN manager.

Context:
- Ubuntu 24.04 VPS
- Domain: neurosmmai.ru
- Xray / VLESS / REALITY
- nginx stream routes HTTPS and Xray through one public 443 using SNI
- Admin panel: /vpn-admin/
- Public user page: /vpn-user-d87a8f94adf802aa6a0f7e35/
- Public subscription path: /vpn-d87a8f94adf802aa6a0f7e35/
- Main code:
  - app/vpn_admin.py
  - app/vpn_user.py
  - app/vpn-manager.py

Hard constraints:
- Do NOT break existing Xray/nginx/REALITY infrastructure.
- Do NOT change public URLs/paths without migration.
- Keep JSON profile as the main user-facing connection method.
- Raw VLESS is fallback only.
- Keep admin pending model: add/toggle/delete/new-code should NOT apply Xray immediately.
- Only “Apply changes” should run vpn-manager apply.
- Output full replacement files, not snippets.
- Do not use regex patches over generated links.
- Do not expose secrets in HTML.

Current issues:
1. Public user page still looks raw and untrustworthy.
2. Too much text and too many technical options are visible to normal users.
3. User flow should be simple:
   - enter access code
   - copy profile
   - import in VPN app from clipboard
4. QR must contain JSON profile URL, not raw VLESS.
5. Raw VLESS/subscription/fallback 8443 must be hidden under troubleshooting/advanced.
6. Logout must reliably clear old cookies for current path and Path=/.
7. Admin invite copy must work on mobile, or provide reliable manual fallback.
8. Admin should show pending changes clearly.
9. Traffic stats must show real Xray stats. If stats unavailable, say “stats unavailable”, not 0 B.
10. Improve security: cookies, CSRF, permissions, rate limiting, no secrets in HTML.

Expected result:
- audit summary
- full replacement files
- install commands
- rollback commands
- manual test checklist

Operational note for rate limiting behind nginx:
- Admin rate-limit logic uses real client IP from proxy headers.
- nginx must pass:
  - `proxy_set_header X-Real-IP $remote_addr;`
  - `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
- In this repo these headers are already present in `nginx/sites-enabled/neurosmm`.

Manual checklist additions:
- unauth `GET /vpn-admin/invite?slug=me` must return login/401, not invite text
- unauth `GET /vpn-admin/qr?slug=me` must require auth
- authenticated `POST` without csrf must return 403
- `rotate-code` must update code immediately and must not create Xray pending apply
- new user with zero traffic should show `0 B` (not “stats unavailable”) when Xray stats API is reachable
