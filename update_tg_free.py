import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove old tg_start_subscription
old_start = '''@router.get("/webhook/sub/tg_start")
async def tg_start_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = "yahoo.com"
    sid = settings.VLESS_SHORT_ID
    vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    
    import base64
    encoded = base64.b64encode(vless_link.encode("utf-8")).decode("utf-8")
    return Response(content=encoded, media_type="text/plain")'''

content = content.replace(old_start, '')

# 2. Insert new tg_free_subscription BEFORE get_subscription
new_tg_free = '''@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = "yahoo.com"
    sid = settings.VLESS_SHORT_ID
    from urllib.parse import quote
    import base64
    
    vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#{quote('🚀 TELEGRAM (Только ТГ)')}"
    dummy_link = f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:80?type=tcp#{quote('ℹ️ ВРЕМЕННЫЙ ПРОФИЛЬ ДЛЯ ТЕЛЕГРАМ')}"
    
    text = "\\n".join([dummy_link, vless_link])
    encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Subscription-Userinfo": "upload=0; download=0; total=1099511627776; expire=0", 
        "profile-update-interval": "12", 
        "profile-title": f"base64:{base64.b64encode('AnKo VPN (Временный)'.encode('utf-8')).decode('utf-8')}"
    }
    return Response(content=encoded, media_type="application/octet-stream", headers=headers)

@router.get("/webhook/sub/{sub_id}")'''

content = content.replace('@router.get("/webhook/sub/{sub_id}")', new_tg_free)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

# 3. Update bootstrap.html to point to tg_free
with open('c:/Users/HIIkatsu/Desktop/vpn/bootstrap.html', 'r', encoding='utf-8') as f:
    html_content = f.read()

html_content = html_content.replace('https://neurosmmai.ru/webhook/sub/tg_start', 'https://neurosmmai.ru/webhook/sub/tg_free')

with open('c:/Users/HIIkatsu/Desktop/vpn/bootstrap.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("Updated python and html")
