from fastapi import APIRouter, Depends, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64
from urllib.parse import quote
from app.db.models import User
from app.api.dependencies.common import get_async_session
from app.core.settings import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, session: AsyncSession = Depends(get_async_session)):
    # Ищем юзера напрямую через сессию, чтобы избежать багов UserService
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    
    if not user or not user.is_active:
        return Response(content="", status_code=403)

    host = settings.WEBHOOK_URL_DOMAIN
    # Жестко фиксируем внешний порт 443 для всех конфигов
    port = 443 
    pbk = getattr(settings, 'VLESS_PUBLIC_KEY', '')
    fp = getattr(settings, 'VLESS_FINGERPRINT', 'chrome')
    sid = getattr(settings, 'VLESS_SHORT_ID', '')

    # Генерируем 3 конфига с разным SNI для внутреннего роутинга в Xray
    url_smart = f"vless://{user.vless_uuid}@{host}:{port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni=www.samsung.com&sid={sid}&flow=xtls-rprx-vision"
    url_usa = f"vless://{user.vless_uuid}@{host}:{port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni=www.microsoft.com&sid={sid}&flow=xtls-rprx-vision"
    url_fin = f"vless://{user.vless_uuid}@{host}:{port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni=www.apple.com&sid={sid}&flow=xtls-rprx-vision"

    configs = [
        f"{url_smart}#{quote('🇪🇺 AUTO (Умный выбор)')}",
        f"{url_usa}#{quote('🇺🇸 США (Чистый IP)')}",
        f"{url_fin}#{quote('🇫🇮 Финляндия (Direct)')}"
    ]
    
    raw_sub = "\n".join(configs)
    b64_link = base64.b64encode(raw_sub.encode("utf-8")).decode("utf-8")

    try:
        from app.api.utils.subscription import get_dynamic_sub_info
        sub_info = await get_dynamic_sub_info(locals())
        headers = {
            "Subscription-Userinfo": sub_info,
            "profile-title": "AnKo VPN",
            "profile-update-interval": "24"
        }
    except Exception:
        headers = {
            "profile-title": "AnKo VPN",
            "profile-update-interval": "24"
        }

    return Response(content=b64_link, media_type="text/plain", headers=headers)

@router.get("/setup")
async def root_instruction(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html")
