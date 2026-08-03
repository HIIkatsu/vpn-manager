import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update vless_link generation to use Finland
old_link = '''clean_port = getattr(settings, "XRAY_RU_CLEAN_PORT", 444)
    clean_sid = getattr(settings, "XRAY_RU_CLEAN_SHORT_ID", sid)
    vless_link = f"vless://{temp_uuid}@{host}:{clean_port}?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={clean_sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'''

new_link = '''finland_host = settings.FINLAND_PUBLIC_IP
    clean_sid = settings.VLESS_SHORT_ID
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
    vless_link = f"vless://{temp_uuid}@{finland_host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={clean_sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'''

content = content.replace(old_link, new_link)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Router updated locally for Finland.")
