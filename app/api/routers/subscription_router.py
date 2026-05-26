from fastapi import APIRouter, Depends, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64
from urllib.parse import quote
from app.db.models import User
from app.api.dependencies.common import get_async_session
from app.core.settings import settings
import logging

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, os: str = "android", session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    
    if not user or not user.is_active:
        return Response(content="", status_code=403)

    host_fin = settings.WEBHOOK_URL_DOMAIN
    cf_domain = "cf.neurosmmai.ru"
    port = 443
    
    pbk = getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sid = getattr(settings, 'VLESS_SHORT_ID', '')

    # Умная подмена отпечатка для iOS
    os_clean = os.lower().strip()
    if os_clean in ["ios", "mac", "apple"]:
        fp = "safari"
    else:
        fp = "chrome"

    # Эшелон 1: TCP Reality
    url_tcp = f"vless://{user.vless_uuid}@{host_fin}:{port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni=www.samsung.com&sid={sid}&flow=xtls-rprx-vision"
    
    # Эшелон 2: Прямой WS
    url_ws_direct = f"vless://{user.vless_uuid}@{host_fin}:{port}?encryption=none&security=tls&type=ws&host={host_fin}&sni={host_fin}&path=%2Fapi-v3-telemetry"
    
    # Эшелон 3: Cloudflare CDN
    # ОТКАТ К ТВОЕМУ СТАРОМУ ФОРМАТУ: address = cf_domain, но host и sni = host_fin (основной домен).
    # Так мультиплексор на сервере узнает пакет и пустит его дальше.
    url_ws_cf = f"vless://{user.vless_uuid}@{cf_domain}:{port}?encryption=none&security=tls&type=ws&host={host_fin}&sni={host_fin}&path=%2Fapi-v3-telemetry"

    configs = [
        f"{url_tcp}#{quote('🚀 AUTO (TCP Reality)')}",
        f"{url_ws_direct}#{quote('🛸 AUTO (WS Direct)')}",
        f"{url_ws_cf}#{quote('🛡️ AUTO (Cloudflare CDN)')}"
    ]
    
    raw_sub = "\n".join(configs)
    b64_link = base64.b64encode(raw_sub.encode("utf-8")).decode("utf-8")
    
    safe_title = "AnKo VPN Smart"

    try:
        from app.api.utils.subscription import get_dynamic_sub_info
        sub_info = await get_dynamic_sub_info(locals())
        headers = {
            "Subscription-Userinfo": sub_info,
            "profile-title": safe_title,
            "profile-update-interval": "24"
        }
    except Exception as e:
        logging.error(f"Info Error: {e}")
        headers = {
            "profile-title": safe_title,
            "profile-update-interval": "24"
        }

    return Response(content=b64_link, media_type="text/plain", headers=headers)

@router.get("/setup")
async def root_instruction(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html")
