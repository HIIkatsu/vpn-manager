import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update vless_link generation
old_link = 'vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'
new_link = '''clean_port = getattr(settings, "XRAY_RU_CLEAN_PORT", 444)
    clean_sid = getattr(settings, "XRAY_RU_CLEAN_SHORT_ID", sid)
    vless_link = f"vless://{temp_uuid}@{host}:{clean_port}?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={clean_sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"'''

content = content.replace(old_link, new_link)

# 2. Add push logic
old_push = 'redis_client.setex(f"tg_temp_key:{temp_uuid}", 7200, "1")'
new_push = '''redis_client.setex(f"tg_temp_key:{temp_uuid}", 7200, "1")
        from app.services.node_sync import NodeSync
        import asyncio
        asyncio.create_task(NodeSync().add_client(telegram_id=f"tg_temp_{temp_uuid}", uuid=temp_uuid))'''

content = content.replace(old_push, new_push)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Router updated locally.")
