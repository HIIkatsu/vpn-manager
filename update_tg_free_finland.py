import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Revert tg_free_subscription to Finland
old_tg_free = '''@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.RUSSIA_BALANCER_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")'''

new_tg_free = '''@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.FINLAND_PUBLIC_IP
    pbk = getattr(settings, 'VLESS_PUBLIC_KEY', '') or settings.XRAY_REALITY_PUBLIC_KEY
    sni = "yahoo.com"'''

content = content.replace(old_tg_free, new_tg_free)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated tg_free to Finland")
