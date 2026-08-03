import re

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_func = '''def inject_temp_users(clients: list, routing: dict):
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
        logger.exception("Failed to inject temp users")'''

new_func = '''def inject_temp_users(clients: list, routing: dict):
    static_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    static_email = "tg_temp_public"
    
    if not any(c.get("id") == static_uuid for c in clients):
        clients.append({"id": static_uuid, "flow": "xtls-rprx-vision", "email": static_email})
    
    # Inject routing rules for the static email
    routing["rules"].insert(0, {"type": "field", "user": [static_email], "domain": ["geosite:telegram"], "outboundTag": "direct"})
    routing["rules"].insert(1, {"type": "field", "user": [static_email], "ip": ["geoip:telegram"], "outboundTag": "direct"})
    routing["rules"].insert(2, {"type": "field", "user": [static_email], "outboundTag": "block"})

    redis_client = get_redis_client()
    if not redis_client: return
    try:
        temp_keys = redis_client.keys("tg_temp_key:*")
        if temp_keys:
            temp_uuids = [k.split("tg_temp_key:")[1] for k in temp_keys]
            temp_emails = []
            for uid in temp_uuids:
                if uid == static_uuid: continue
                clients.append({"id": uid, "flow": "xtls-rprx-vision", "email": f"tg_temp_{uid}"})
                temp_emails.append(f"tg_temp_{uid}")
            if temp_emails:
                routing["rules"].insert(0, {"type": "field", "user": temp_emails, "domain": ["geosite:telegram"], "outboundTag": "direct"})
                routing["rules"].insert(1, {"type": "field", "user": temp_emails, "ip": ["geoip:telegram"], "outboundTag": "direct"})
                routing["rules"].insert(2, {"type": "field", "user": temp_emails, "outboundTag": "block"})
    except Exception as e:
        logger.exception("Failed to inject temp users")'''

content = content.replace(old_func, new_func)

with open('c:/Users/HIIkatsu/Desktop/vpn/subscription_router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated inject_temp_users")
