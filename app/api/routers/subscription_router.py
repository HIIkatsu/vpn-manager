from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
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


def _build_subscription_url(user: User, os_name: str) -> str:
    return f"https://{settings.WEBHOOK_URL_DOMAIN}/webhook/sub/{user.vless_uuid}?os={os_name}"


def _build_hiddify_deeplink(sub_url: str) -> str:
    return f"hiddify://install-sub?url={quote(sub_url, safe='')}"

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


@router.get("/cabinet/{uuid}")
async def web_cabinet(request: Request, uuid: str, session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user:
        return Response(content="Профиль не найден", status_code=404)

    os_name = (request.query_params.get("os") or user.preferred_os or "android").lower()
    user.preferred_os = os_name
    await session.commit()

    sub_url = _build_subscription_url(user, os_name)
    context = {
        "request": request,
        "user": user,
        "os_name": os_name,
        "sub_url": sub_url,
        "hiddify_deeplink": _build_hiddify_deeplink(sub_url),
    }
    return templates.TemplateResponse(request=request, name="cabinet.html", context=context)


@router.get("/cabinet/{uuid}/pay/{amount}")
async def web_cabinet_pay(uuid: str, amount: float, session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user:
        return Response(content="Профиль не найден", status_code=404)

    if amount not in {100.0, 250.0, 900.0}:
        return Response(content="Неверный тариф", status_code=400)

    from app.core.container import get_billing_service

    billing = get_billing_service(session)
    confirmation_url = await billing.create_subscription_payment(user_id=user.id, amount=amount, return_url=f"https://{settings.WEBHOOK_URL_DOMAIN}/cabinet/{uuid}?payment=success")
    await session.commit()
    return RedirectResponse(url=confirmation_url, status_code=302)
