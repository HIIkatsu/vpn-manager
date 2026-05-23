from fastapi import APIRouter, Depends, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.common import get_async_session
from app.api.utils.subscription import get_dynamic_sub_info
from app.services.user_service import UserService
from app.services.xray_manager import XrayManager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, session: AsyncSession = Depends(get_async_session)):
    user_service = UserService(session)
    user = await user_service.get_by_uuid(uuid, session=session)
    if not user or not user.is_active:
        return Response(content="", status_code=403)
    xray = XrayManager()
    link = xray.generate_vless_subscription(user.vless_uuid)
    b64_link = __import__("base64").b64encode(link.encode("utf-8")).decode("utf-8")
    sub_info = await get_dynamic_sub_info(locals())
    return Response(content=b64_link, media_type="text/plain", headers={"Subscription-Userinfo": sub_info})


@router.get("/setup")
async def root_instruction(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html")
