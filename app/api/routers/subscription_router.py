from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64
import math
import json
import hmac
import ipaddress
from urllib.parse import quote
from datetime import datetime, timezone
from app.db.models import User
from app.api.dependencies.common import get_read_session, get_write_session
from app.core.settings import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

def format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes == 0: return "0 B"
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][i]}"

def _build_subscription_url(request: Request, user: User) -> str:
    return f"https://{getattr(settings, 'WEBHOOK_URL_DOMAIN', request.url.hostname)}/webhook/sub/{user.vless_uuid}"

def _build_hiddify_deeplink(sub_url: str) -> str:
    return f"hiddify://install-config?url={quote(sub_url, safe='')}"

@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, os: str = "android", session: AsyncSession = Depends(get_read_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user or not user.is_active: return Response(content="", status_code=403)

    host_fin_domain = settings.WEBHOOK_URL_DOMAIN
    host_fin_ip = settings.FINLAND_PUBLIC_IP
    host_de = settings.GERMANY_PUBLIC_IP
    host_nl = settings.NETHERLANDS_PUBLIC_IP
    host_ru = settings.RUSSIA_BALANCER_IP

    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sid = settings.VLESS_SHORT_ID
    fp = "safari" if os.lower().strip() in ["ios", "mac", "apple"] else "chrome"

    def make_tcp(host, name, target_port=443, custom_sni="www.samsung.com", custom_sid=sid):
        return f"vless://{user.vless_uuid}@{host}:{target_port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni={custom_sni}&sid={custom_sid}&flow=xtls-rprx-vision#{quote(name)}"

    fake = "00000000-0000-0000-0000-000000000000"
    divider = lambda text: f"vless://{fake}@127.0.0.1:80?type=tcp#{quote(text)}"

    configs = [
        divider("▼ 💎 РЕКОМЕНДУЕМ ▼"),
        make_tcp(host_ru, "🇪🇺 ✨ Умный профиль"),
        make_tcp(host_fin_ip, "🇪🇺 ⚡ Турбо-скорость", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_ru, "🇪🇺 🛡️ LTE / 4G Анти-глушилка", target_port=settings.XRAY_RU_WHITELIST_PORT, custom_sid=settings.XRAY_RU_WHITELIST_SHORT_ID, custom_sni="vk.com"),
        divider("▼ 🆘 ДЛЯ МОБИЛЬНОГО ▼"),
        make_tcp(host_fin_ip, "🇫🇮 Финляндия 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_de, "🇩🇪 Германия 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_nl, "🇳🇱 Нидерланды 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_fin_ip, "🇬🇧 Великобритания", target_port=2083),
        divider("▼ 🌍 ДЛЯ WI-FI ▼"),
        make_tcp(host_fin_domain, "🇫🇮 Финляндия 1"),
        make_tcp(host_de, "🇩🇪 Германия 1"),
        make_tcp(host_nl, "🇳🇱 Нидерланды 1"),
        make_tcp(host_fin_domain, "🇸🇪 Швеция"),
        make_tcp(host_ru, "🇷🇺 Россия (Без VPN)", target_port=settings.XRAY_RU_CLEAN_PORT, custom_sid=settings.XRAY_RU_CLEAN_SHORT_ID, custom_sni="ya.ru"),
        divider("▼ 🚀 ДЛЯ СЕРВИСОВ ▼"),
        make_tcp(host_fin_domain, "🇺🇸 📺 YouTube 4K"),
        make_tcp(host_fin_domain, "🇺🇸 🤖 ChatGPT"),
        make_tcp(host_nl, "🇺🇸 📸 Insta / TikTok")
    ]

    if user.sub_end_date:
        now = datetime.now(timezone.utc)
        end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
        days_left = (end_date - now).days
        if 0 <= days_left <= 3:
            if days_left == 1:
                d_str = "ОСТАЛСЯ 1 ДЕНЬ"
            elif days_left in [2, 3]:
                d_str = f"ОСТАЛОСЬ {days_left} ДНЯ"
            else:
                d_str = "ОСТАЛОСЬ МЕНЕЕ 1 ДНЯ"
            configs.insert(0, divider(f"⚠️ {d_str} ПОДПИСКИ!"))

    sub_info = f"upload=0; download={user.traffic_total_bytes or 0}; total=1099511627776; expire={int(user.sub_end_date.timestamp()) if user.sub_end_date else 0}"
    profile_title = base64.b64encode("🚀 AnKo Smart VPN".encode("utf-8")).decode("utf-8")

    headers = {
        "Subscription-Userinfo": sub_info,
        "profile-update-interval": "12",
        "profile-title": f"base64:{profile_title}"
    }

    return Response(content=base64.b64encode("\n".join(configs).encode("utf-8")).decode("utf-8"), media_type="text/plain", headers=headers)

@router.get("/setup")
async def root_instruction(request: Request): return templates.TemplateResponse(request=request, name="setup.html")

@router.get("/cabinet/{uuid}")
async def web_cabinet(request: Request, uuid: str, session: AsyncSession = Depends(get_write_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user: return Response(content="Профиль не найден", status_code=404)
    user.preferred_os = (request.query_params.get("os") or user.preferred_os or "android").lower()
    await session.commit()
    
    now = datetime.now(timezone.utc)
    end_date = user.sub_end_date
    if end_date and end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    days_left = (end_date - now).days if end_date else 0
    days_left = max(0, days_left)
    end_date_str = end_date.strftime("%d.%m.%Y") if end_date else "Нет данных"
    traffic_bytes = user.traffic_total_bytes or 0
    formatted_traffic = format_bytes(traffic_bytes)
    traffic_percent = min(100, round((traffic_bytes / 1099511627776) * 100, 1))
    
    return templates.TemplateResponse(request=request, name="cabinet.html", context={
        "request": request, "user": user,
        "sub_url": _build_subscription_url(request, user),
        "hiddify_deeplink": _build_hiddify_deeplink(_build_subscription_url(request, user)),
        "days_left": days_left, "end_date_str": end_date_str,
        "formatted_traffic": formatted_traffic, "traffic_percent": traffic_percent,
        "os_name": user.preferred_os
    })

def verify_sync_token(request: Request):
    auth = request.headers.get("authorization", "")
    sync_token = settings.SYNC_NODES_TOKEN.strip()
    if not sync_token or not hmac.compare_digest(auth, f"Bearer {sync_token}"):
        raise HTTPException(status_code=403, detail="Forbidden")

@router.get("/webhook/sync-nodes-777")
async def generate_nodes_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    verify_sync_token(request)
    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    clients.append({"id": "11111111-1111-1111-1111-111111111111", "flow": "xtls-rprx-vision", "email": "transit_node_ru"})
    
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    config = {
      "log": {"loglevel": "warning"},
      "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
      "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}],
      "routing": {"rules": [{"inboundTag": ["api"], "outboundTag": "api", "type": "field"}]},
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": settings.XRAY_MAIN_PORT, "protocol": "vless", "tag": "vless-smart",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "www.samsung.com:443", "xver": 0, "serverNames": ["www.samsung.com"], "privateKey": prv, "shortIds": [sid]}}
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_CLEAN_PORT, "protocol": "vless", "tag": "vless-ru-clean",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "ya.ru:443", "xver": 0, "serverNames": ["ya.ru", "yandex.ru"], "privateKey": prv, "shortIds": [settings.XRAY_RU_CLEAN_SHORT_ID]}}
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_WHITELIST_PORT, "protocol": "vless", "tag": "vless-ru-whitelist",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "vk.com:443", "xver": 0, "serverNames": ["vk.com", "m.vk.com"], "privateKey": prv, "shortIds": [settings.XRAY_RU_WHITELIST_SHORT_ID]}}
        }
      ]
    }
    return Response(content=json.dumps(config, indent=2), media_type="application/json")

@router.get("/webhook/sync-transit-777")
async def generate_transit_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    verify_sync_token(request)
    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')

    config = {
      "log": {"loglevel": "warning"},
      "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "vless-smart-transit",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "www.samsung.com:443", "xver": 0,
                  "serverNames": ["www.samsung.com"],
                  "privateKey": prv, "shortIds": [sid]
              }
          }
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_CLEAN_PORT, "protocol": "vless", "tag": "vless-ru-clean",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "ya.ru:443", "xver": 0,
                  "serverNames": ["ya.ru", "yandex.ru"],
                  "privateKey": prv, "shortIds": [settings.XRAY_RU_CLEAN_SHORT_ID]
              }
          }
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_WHITELIST_PORT, "protocol": "vless", "tag": "vless-ru-whitelist",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "vk.com:443", "xver": 0,
                  "serverNames": ["vk.com", "m.vk.com"],
                  "privateKey": prv, "shortIds": [settings.XRAY_RU_WHITELIST_SHORT_ID]
              }
          }
        }
      ],
      "outbounds": [
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
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
        },
        {
          "protocol": "vless", "tag": "eu-nl",
          "settings": {"vnext": [{"address": settings.NETHERLANDS_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
        }
      ],
      "routing": {
        "domainStrategy": "IPIfNonMatch",
        "balancers": [{"tag": "eu-balancer", "selector": ["eu-"]}],
        "rules": [
          {"inboundTag": ["api"], "outboundTag": "api", "type": "field"},
          {"inboundTag": ["vless-ru-clean"], "outboundTag": "direct", "type": "field"},
          {"type": "field", "outboundTag": "direct", "domain": ["domain:ru", "domain:su", "domain:рф", "domain:vk.com", "domain:yandex.ru", "domain:ya.ru", "domain:mail.ru", "domain:sberbank.ru", "domain:gosuslugi.ru"]},
          {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
          {"type": "field", "balancerTag": "eu-balancer", "network": "tcp,udp"}
        ]
      }
    }
    return Response(content=json.dumps(config, indent=2), media_type="application/json")
