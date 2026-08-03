import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

STATIC_UUID = "b5a71a3c-1111-2222-3333-000000000000"

# 1. Update bootstrap_page to just return the template, no need to generate temp_uuid
old_bootstrap = '''@router.get("/start")
async def bootstrap_page(request: Request):
    temp_uuid = str(uuid.uuid4())
    redis_client = get_redis_client()
    if redis_client:
        try:
            redis_client.setex(f"tg_temp_key:{temp_uuid}", 7200, "1")
            from app.services.node_sync import NodeSync
            import asyncio
            asyncio.create_task(NodeSync().add_client(telegram_id=f"tg_temp_{temp_uuid}", uuid=temp_uuid))
        except Exception:
            pass
    
    host = settings.RUSSIA_BALANCER_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = getattr(settings, 'VLESS_SNI', 'www.samsung.com')
    sid = settings.VLESS_SHORT_ID
    
    finland_host = settings.FINLAND_PUBLIC_IP
    clean_sid = settings.VLESS_SHORT_ID
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
    vless_link = f"vless://{temp_uuid}@{finland_host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={clean_sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    return templates.TemplateResponse(request=request, name="bootstrap.html", context={"vless_link": vless_link, "temp_uuid": temp_uuid})'''

new_bootstrap = f'''@router.get("/start")
async def bootstrap_page(request: Request):
    # Static UUID for all Telegram bypass users
    temp_uuid = "{STATIC_UUID}"
    
    # Push the static UUID to nodes asynchronously just in case it's missing
    from app.services.node_sync import NodeSync
    import asyncio
    asyncio.create_task(NodeSync().add_client(telegram_id="tg_temp_public", uuid=temp_uuid))
    
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = "yahoo.com"
    sid = settings.VLESS_SHORT_ID
    
    vless_link = f"vless://{{temp_uuid}}@{{host}}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={{pbk}}&sni={{sni}}&sid={{sid}}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    return templates.TemplateResponse(request=request, name="bootstrap.html", context={{"vless_link": vless_link, "temp_uuid": temp_uuid}})

@router.get("/webhook/sub/tg_start")
async def tg_start_subscription(request: Request):
    temp_uuid = "{STATIC_UUID}"
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = "yahoo.com"
    sid = settings.VLESS_SHORT_ID
    vless_link = f"vless://{{temp_uuid}}@{{host}}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={{pbk}}&sni={{sni}}&sid={{sid}}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    
    import base64
    encoded = base64.b64encode(vless_link.encode("utf-8")).decode("utf-8")
    return Response(content=encoded, media_type="text/plain")
'''

content = content.replace(old_bootstrap, new_bootstrap)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated router")
