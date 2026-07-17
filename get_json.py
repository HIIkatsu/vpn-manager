import sys
sys.path.insert(0, "/root/vpn-manager-v2")

import asyncio, json
from sqlalchemy.future import select
from app.api.dependencies.common import get_read_session
from app.db.models import User
from app.core.settings import settings

async def generate():
    async for session in get_read_session():
        users = (await session.execute(select(User))).scalars().all()
        clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
        break
        
    prv = settings.XRAY_REALITY_PRIVATE_KEY
    sid = settings.VLESS_SHORT_ID
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')

    config = {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
        "observatory": {
            "subjectSelector": ["eu-"],
            "probeURL": "http://cp.cloudflare.com/generate_204",
            "probeInterval": "1m",
            "enableConcurrency": True
        },
        "inbounds": [
            {"tag": "api-in", "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1", "port": 65535, "network": "tcp"}},
            {
                "listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "vless-smart-transit",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "www.samsung.com:443", "xver": 0, "serverNames": ["www.samsung.com"], "privateKey": prv, "shortIds": [sid]}}
            },
            {
                "listen": "0.0.0.0", "port": int(settings.XRAY_RU_CLEAN_PORT), "protocol": "vless", "tag": "vless-ru-clean",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "ya.ru:443", "xver": 0, "serverNames": ["ya.ru", "yandex.ru"], "privateKey": prv, "shortIds": [settings.XRAY_RU_CLEAN_SHORT_ID]}}
            },
            {
                "listen": "0.0.0.0", "port": int(settings.XRAY_RU_WHITELIST_PORT), "protocol": "vless", "tag": "vless-ru-whitelist",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "vk.com:443", "xver": 0, "serverNames": ["vk.com", "m.vk.com"], "privateKey": prv, "shortIds": [settings.XRAY_RU_WHITELIST_SHORT_ID]}}
            }
        ],
        "outbounds": [
            {"protocol": "api", "tag": "api"},
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
            {
                "protocol": "vless", "tag": "eu-fin",
                "settings": {"vnext": [{"address": settings.FINLAND_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
            },
            {
                "protocol": "vless", "tag": "eu-ger",
                "settings": {"vnext": [{"address": settings.GERMANY_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "wikipedia.org", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
            },
            {
                "protocol": "vless", "tag": "eu-nl",
                "settings": {"vnext": [{"address": settings.NETHERLANDS_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
                "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "yahoo.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
            }
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "balancers": [{"tag": "eu-balancer", "selector": ["eu-"], "strategy": {"type": "leastPing"}}],
            "rules": [
                {"inboundTag": ["api-in"], "outboundTag": "api", "type": "field"},
                {"inboundTag": ["vless-ru-clean"], "outboundTag": "direct", "type": "field"},
                {"type": "field", "outboundTag": "direct", "domain": ["domain:ru", "domain:su", "domain:xn--p1ai", "domain:vk.com", "domain:yandex.ru", "domain:ya.ru", "domain:mail.ru", "domain:sberbank.ru", "domain:gosuslugi.ru"]},
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
                {"type": "field", "balancerTag": "eu-balancer", "network": "tcp,udp"}
            ]
        }
    }
    with open("/root/russia_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print("[+] Эталонный конфиг сгенерирован в обход API!")

asyncio.run(generate())
