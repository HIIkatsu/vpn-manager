import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from app.api.dependencies.common import get_async_session, get_current_admin
from app.api.utils.subscription import format_bytes
from app.db.models import PendingAction, User
from app.services.traffic_stats_service import TrafficStatsService
from app.services.user_service import UserService
from app.services.xray_manager import XrayManager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    q: str = None,
    username: str = Depends(get_current_admin),
    session: AsyncSession = Depends(get_async_session),
):
    xray = XrayManager()
    stats_task = asyncio.create_task(xray.get_live_traffic_stats(reset=True))

    stmt = select(User).order_by(User.telegram_id)
    if q:
        stmt = stmt.where(cast(User.telegram_id, String).like(f"%{q.strip()}%"))

    users_task = asyncio.create_task(session.execute(stmt))
    pending_task = asyncio.create_task(session.execute(select(PendingAction).options(joinedload(PendingAction.user))))

    live_stats = await stats_task
    users_db = (await users_task).scalars().all()
    pending_actions = (await pending_task).scalars().all()

    now = datetime.now(timezone.utc)
    total_bytes = 0
    active_users = 0
    users_data = []

    for u in users_db:
        used_bytes = live_stats.get(str(u.telegram_id), 0)
        total_used_bytes = await TrafficStatsService.persist_and_get_total(session, u.telegram_id, used_bytes)
        total_bytes += total_used_bytes

        is_currently_active = u.is_active and (u.sub_end_date is None or u.sub_end_date >= now)
        if is_currently_active:
            active_users += 1

        days_left = None
        if u.sub_end_date:
            delta = u.sub_end_date - now
            days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))

        users_data.append(
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "vless_uuid": u.vless_uuid,
                "masked_uuid": f"{str(u.vless_uuid)[:8]}************{str(u.vless_uuid)[-4:]}",
                "is_active": is_currently_active,
                "sub_end_date_obj": u.sub_end_date,
                "sub_end_date": u.sub_end_date.strftime("%d.%m.%Y %H:%M") if u.sub_end_date else "Безлимит",
                "days_left": days_left,
                "traffic": format_bytes(total_used_bytes),
                "traffic_percent": round(min(100.0, (total_used_bytes / (1024**4)) * 100), 2),
            }
        )

    pending_data = []
    for p in pending_actions:
        tg_id = p.user.telegram_id if p.user else (p.payload.get("telegram_id", "Новый") if p.payload else "Новый")
        pending_data.append({"action_type": p.action_type, "tg_id": tg_id})

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "request": request,
            "users": users_data,
            "total_users": len(users_db),
            "active_users": active_users,
            "total_traffic": format_bytes(total_bytes),
            "traffic_percent": round(min(100.0, (total_bytes / (1024**4)) * 100), 2),
            "pending": pending_data,
            "query": q or "",
        },
    )


@router.post("/admin/user/add")
async def admin_user_add(
    telegram_id: str = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)
):
    action = PendingAction(action_type="add", payload={"telegram_id": telegram_id, "vless_uuid": str(uuid4())})
    session.add(action)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/user/toggle")
async def admin_user_toggle(
    user_id: int = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)
):
    user = await session.get(User, user_id)
    if user:
        user.is_active = not user.is_active
        action_type = "toggle_enable" if user.is_active else "toggle_disable"
        action = PendingAction(action_type=action_type, user_id=user.id)
        session.add(action)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/user/add_days")
async def admin_user_add_days(
    user_id: int = Form(...),
    days: int = Form(...),
    session: AsyncSession = Depends(get_async_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user and days > 0:
        now = datetime.now(timezone.utc)
        base_date = user.sub_end_date if user.sub_end_date and user.sub_end_date > now else now
        user.sub_end_date = base_date + timedelta(days=days)
        user.is_active = True
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/user/set_infinite")
async def admin_user_set_infinite(
    user_id: int = Form(...),
    session: AsyncSession = Depends(get_async_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user:
        user.sub_end_date = None
        user.is_active = True
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/user/reset_traffic")
async def admin_user_reset_traffic(
    user_id: int = Form(...),
    session: AsyncSession = Depends(get_async_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user:
        user.traffic_total_bytes = 0
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/user/delete")
async def admin_user_delete(
    user_id: int = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)
):
    user = await session.get(User, user_id)
    if user:
        action = PendingAction(action_type="delete", user_id=user.id)
        session.add(action)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/apply")
async def admin_apply(session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)):
    result = await session.execute(select(PendingAction))
    actions = result.scalars().all()
    if not actions:
        return RedirectResponse(url="/admin", status_code=303)
    xray = XrayManager()
    user_service = UserService(session)
    for action in actions:
        success = False
        try:
            if action.action_type == "add":
                p = action.payload
                user = await user_service.get_by_telegram_id(int(p["telegram_id"]))
                if not user:
                    user = await user_service.create_user(telegram_id=int(p["telegram_id"]), vless_uuid=p["vless_uuid"])
                success = await xray.add_client(email=str(user.telegram_id), uuid=str(user.vless_uuid))
            elif action.action_type in ("toggle_disable", "toggle_enable", "delete"):
                user = await session.get(User, action.user_id)
                if user:
                    if action.action_type == "toggle_disable" or action.action_type == "delete":
                        success = await xray.remove_client(email=str(user.telegram_id))
                    else:
                        success = await xray.add_client(email=str(user.telegram_id), uuid=str(user.vless_uuid))
                    if action.action_type == "delete" and success:
                        await session.delete(user)
            if success or action.action_type == "delete":
                await session.delete(action)
        except Exception as e:
            print(f"Apply failed for action {action.id}: {e}")
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/admin/logout")
async def admin_logout():
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
