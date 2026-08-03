import sys
import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add imports for UUID and Redis
if 'import uuid' not in content:
    content = content.replace('import base64, math, json, hmac, urllib.parse, hashlib, time, aiohttp, asyncio, logging', 'import base64, math, json, hmac, urllib.parse, hashlib, time, aiohttp, asyncio, logging, uuid')

# Add get_redis_client and inject_temp_users and /start
injection_code = """
try:
    from redis import Redis
except Exception:
    Redis = None

def get_redis_client():
    if settings.REDIS_URL and Redis:
        return Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    return None

def inject_temp_users(clients: list, routing: dict):
    redis_client = get_redis_client()
    if not redis_client: return
    try:
        temp_keys = redis_client.keys("tg_temp_key:*")
        if temp_keys:
            temp_uuids = [k.split("tg_temp_key:")[1] for k in temp_keys]
            temp_emails = []
            for uid in temp_uuids:
                clients.append({"id": uid, "flow": "xtls-rprx-vision", "email": f"tg_temp_{uid}"})
                temp_emails.append(f"tg_temp_{uid}")
            
            routing["rules"].insert(0, {"type": "field", "user": temp_emails, "domain": ["geosite:telegram"], "outboundTag": "direct"})
            routing["rules"].insert(1, {"type": "field", "user": temp_emails, "ip": ["geoip:telegram"], "outboundTag": "direct"})
            routing["rules"].insert(2, {"type": "field", "user": temp_emails, "outboundTag": "block"})
    except Exception as e:
        logger.exception("Failed to inject temp users")

@router.get("/start")
async def bootstrap_page(request: Request):
    temp_uuid = str(uuid.uuid4())
    redis_client = get_redis_client()
    if redis_client:
        try:
            redis_client.setex(f"tg_temp_key:{temp_uuid}", 7200, "1")
        except Exception:
            pass
    
    host = settings.RUSSIA_BALANCER_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = getattr(settings, 'VLESS_SNI', 'www.samsung.com')
    sid = settings.VLESS_SHORT_ID
    
    vless_link = f"vless://{temp_uuid}@{host}:443?type=tcp&security=reality&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    return templates.TemplateResponse(request=request, name="bootstrap.html", context={"vless_link": vless_link, "temp_uuid": temp_uuid})

# --- ИСПРАВЛЕННЫЕ ГЕНЕРАТОРЫ XRAY ---
"""

content = content.replace('# --- ИСПРАВЛЕННЫЕ ГЕНЕРАТОРЫ XRAY ---', injection_code.strip())

# Inject into generate_nodes_config
content = content.replace(
    'return Response(content=json.dumps(config, indent=2), media_type="application/json")',
    'inject_temp_users(clients, config["routing"])\n    return Response(content=json.dumps(config, indent=2), media_type="application/json")'
)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Router updated locally.")
