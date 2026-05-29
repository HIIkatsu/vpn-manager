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
from app.core.security import ip_in_allowlist

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
    
    host_fin_domain = getattr(settings, 'WEBHOOK_URL_DOMAIN', 'neurosmmai.ru')
    host_fin_ip = "150.251.152.174"
    host_de = "132.243.194.119"
    host_nl = "194.50.94.177"
    host_ru = "132.243.230.173"
    
    pbk = settings.VLESS_PUBLIC_KEY
    sid = settings.VLESS_SHORT_ID
    fp = "safari" if os.lower().strip() in ["ios", "mac", "apple"] else "chrome"
    
    def make_tcp(host, name, target_port=443, custom_sni="www.samsung.com", custom_sid=sid):
        return f"vless://{user.vless_uuid}@{host}:{target_port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni={custom_sni}&sid={custom_sid}&flow=xtls-rprx-vision#{quote(name)}"
    
    fake = "00000000-0000-0000-0000-000000000000"
    divider = lambda text: f"vless://{fake}@127.0.0.1:80?type=tcp#{quote(text)}"
    
    configs = [
        divider("▼ 💎 РЕКОМЕНДУЕМ ▼"),
        make_tcp(host_ru, "🇪🇺 ⚖️ Балансир"),
        make_tcp(host_fin_ip, "🇪🇺 ⚡ Турбо-скорость", target_port=20443),
        make_tcp(host_ru, "🇪🇺 🛡️ LTE / 4G Анти-глушилка", custom_sid="45b6b57266629594", custom_sni="vk.com"),
        divider("▼ 🆘 ДЛЯ МОБИЛЬНОГО ▼"),
        make_tcp(host_fin_ip, "🇫🇮 Финляндия 2", target_port=20443),
        make_tcp(host_de, "🇩🇪 Германия 2", target_port=20443),
        make_tcp(host_nl, "🇳🇱 Нидерланды 2", target_port=20443),
        make_tcp(host_fin_ip, "🇬🇧 Великобритания", target_port=2083),
        divider("▼ 🌍 ДЛЯ WI-FI ▼"),
        make_tcp(host_fin_domain, "🇫🇮 Финляндия 1"),
        make_tcp(host_de, "🇩🇪 Германия 1"),
        make_tcp(host_nl, "🇳🇱 Нидерланды 1"),
        make_tcp(host_fin_domain, "🇸🇪 Швеция"),
        make_tcp(host_ru, "🇷🇺 Россия (Без VPN)", custom_sid="45b6b57266629593", custom_sni="ya.ru"),
        divider("▼ 🚀 ДЛЯ СЕРВИСОВ ▼"),
        make_tcp(host_fin_domain, "🇺🇸 📺 YouTube 4K"),
        make_tcp(host_fin_domain, "🇺🇸 🤖 ChatGPT"),
        make_tcp(host_nl, "🇺🇸 📸 Insta / TikTok")
    ]
    sub_info = f"upload=0; download={user.traffic_total_bytes or 0}; total=1099511627776; expire={int(user.sub_end_date.timestamp()) if user.sub_end_date else 0}"
    return Response(content=base64.b64encode("\n".join(configs).encode("utf-8")).decode("utf-8"), media_type="text/plain", headers={"Subscription-Userinfo": sub_info, "profile-update-interval": "12"})

@router.get("/setup")
async def root_instruction(request: Request): return templates.TemplateResponse(request=request, name="setup.html")

@router.get("/cabinet/{uuid}")
async def web_cabinet(request: Request, uuid: str, session: AsyncSession = Depends(get_write_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user: return Response(content="Профиль не найден", status_code=404)
    user.preferred_os = (request.query_params.get("os") or user.preferred_os or "android").lower()
    return templates.TemplateResponse(request=request, name="cabinet.html", context={"request": request, "user": user, "sub_url": _build_subscription_url(request, user), "hiddify_deeplink": _build_hiddify_deeplink(_build_subscription_url(request, user))})

@router.get("/webhook/sync-nodes-777")
async def generate_nodes_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    auth = request.headers.get("authorization", "")
    sync_token = settings.SYNC_NODES_TOKEN.strip()
    if not sync_token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sync token is not configured")

    expected = f"Bearer {sync_token}"
    if not hmac.compare_digest(auth, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    trusted_proxies = {ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()}
    remote_addr = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    x_real_ip = request.headers.get("x-real-ip")
    client_ip = remote_addr
    if remote_addr in trusted_proxies:
        if x_real_ip:
            client_ip = x_real_ip.strip()
        elif forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    cidrs = [x.strip() for x in settings.SYNC_NODES_IP_ALLOWLIST.split(",") if x.strip()]
    if not cidrs or not ip_in_allowlist(client_ip, cidrs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    
    config = {
      "log": {"loglevel": "warning"},
      "inbounds": [
        {
          "listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "vless-smart",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "www.samsung.com:443", "xver": 0, "serverNames": ["www.samsung.com"], "privateKey": prv, "shortIds": [sid]}}
        },
        {
          "listen": "0.0.0.0", "port": 10444, "protocol": "vless", "tag": "vless-ru-clean",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "ya.ru:443", "xver": 0, "serverNames": ["ya.ru", "yandex.ru"], "privateKey": prv, "shortIds": ["45b6b57266629593"]}}
        },
        {
          "listen": "0.0.0.0", "port": 10445, "protocol": "vless", "tag": "vless-ru-whitelist",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "vk.com:443", "xver": 0, "serverNames": ["vk.com", "m.vk.com"], "privateKey": prv, "shortIds": ["45b6b57266629594"]}}
        }
      ]
    }
    return Response(content=json.dumps(config, indent=2), media_type="application/json")
