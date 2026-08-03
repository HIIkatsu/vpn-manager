import sys
import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the vless_link generation
old_link = 'vless_link = f"vless://{temp_uuid}@{host}:443?type=tcp&security=reality&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'
new_link = 'vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'

content = content.replace(old_link, new_link)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Router VLESS link updated locally.")
