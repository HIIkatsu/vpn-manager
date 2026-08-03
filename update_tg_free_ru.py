import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update tg_free_subscription host and sni
old_tg_free = '''@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = "yahoo.com"
    sid = settings.VLESS_SHORT_ID'''

new_tg_free = '''@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.RUSSIA_BALANCER_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
    sid = settings.VLESS_SHORT_ID'''

content = content.replace(old_tg_free, new_tg_free)

# 2. Update inject_temp_users domain whitelist
old_inject = '''    # Inject routing rules for the static email
    routing["rules"].insert(0, {"type": "field", "user": [static_email], "domain": ["geosite:telegram"], "outboundTag": "direct"})'''

new_inject = '''    # Inject routing rules for the static email
    routing["rules"].insert(0, {"type": "field", "user": [static_email], "domain": ["geosite:telegram", "domain:cloudflare.com", "geosite:google"], "outboundTag": "direct"})'''

content = content.replace(old_inject, new_inject)

old_inject_2 = '''            if temp_emails:
                routing["rules"].insert(0, {"type": "field", "user": temp_emails, "domain": ["geosite:telegram"], "outboundTag": "direct"})'''

new_inject_2 = '''            if temp_emails:
                routing["rules"].insert(0, {"type": "field", "user": temp_emails, "domain": ["geosite:telegram", "domain:cloudflare.com", "geosite:google"], "outboundTag": "direct"})'''

content = content.replace(old_inject_2, new_inject_2)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated tg_free to Russia and allowed ping checks")
