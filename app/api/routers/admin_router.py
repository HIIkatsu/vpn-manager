import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import uuid4
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, String, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload
from app.api.dependencies.common import get_read_session, get_write_session, get_current_admin
from app.api.utils.subscription import format_bytes
from app.core.settings import settings
from app.db.models import PendingAction, User
from app.db.models.promocode import Promocode
from app.db.database import async_session_maker
from app.services.transaction import session_scope
from app.services.traffic_stats_service import TrafficStatsService
from app.services.user_lifecycle import delete_user_with_relations
from app.services.xray_manager import XrayManager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    q: str = None,
    username: str = Depends(get_current_admin),
    session: AsyncSession = Depends(get_read_session),
):
    xray = XrayManager()
    stats_task = asyncio.create_task(xray.get_live_traffic_stats(reset=False))
    
    stmt = select(User).order_by(User.telegram_id)
    if q:
        search_query = f"%{q.strip()}%"
        # Умный поиск: по ID или по Username в базе
        stmt = stmt.where(
            or_(
                cast(User.telegram_id, String).like(search_query),
                User.username.like(search_query)
            )
        )
        
    live_stats = await stats_task
    users_db = (await session.execute(stmt)).scalars().all()
    pending_actions = (await session.execute(select(PendingAction).options(joinedload(PendingAction.user)))).scalars().all()
    promos_db = (await session.execute(select(Promocode).order_by(Promocode.id.desc()))).scalars().all()

    now = datetime.now(timezone.utc)
    total_bytes = 0
    active_users = 0
    users_data = []
    
    for u in users_db:
        used_bytes = live_stats.get(str(u.telegram_id), 0)
        total_used_bytes = await TrafficStatsService.get_total_with_live(session, u.telegram_id, used_bytes)
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
                "username": u.username,
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
        pending_data.append({"id": p.id, "action_type": p.action_type, "tg_id": tg_id})
        
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
            "promocodes": promos_db,
            "query": q or "",
        },
    )

@router.post("/admin/user/add")
async def admin_user_add(
    telegram_id: str = Form(...), session: AsyncSession = Depends(get_write_session), admin=Depends(get_current_admin)
):
    action = PendingAction(action_type="add", payload={"telegram_id": telegram_id, "vless_uuid": str(uuid4())})
    session.add(action)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/toggle")
async def admin_user_toggle(
    user_id: int = Form(...), session: AsyncSession = Depends(get_write_session), admin=Depends(get_current_admin)
):
    user = await session.get(User, user_id)
    if user:
        action_type = "toggle_disable" if user.is_active else "toggle_enable"
        action = PendingAction(action_type=action_type, user_id=user.id)
        session.add(action)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/add_days")
async def admin_user_add_days(
    user_id: int = Form(...),
    days: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user and days > 0:
        now = datetime.now(timezone.utc)
        base_date = user.sub_end_date if user.sub_end_date and user.sub_end_date > now else now
        user.sub_end_date = base_date + timedelta(days=days)
        session.add(PendingAction(action_type="toggle_enable", user_id=user.id))
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/set_infinite")
async def admin_user_set_infinite(
    user_id: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user:
        user.sub_end_date = None
        session.add(PendingAction(action_type="toggle_enable", user_id=user.id))
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/reset_traffic")
async def admin_user_reset_traffic(
    user_id: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin),
):
    user = await session.get(User, user_id)
    if user:
        user.traffic_total_bytes = 0
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/delete")
async def admin_user_delete(
    user_id: int = Form(...), session: AsyncSession = Depends(get_write_session), admin=Depends(get_current_admin)
):
    user = await session.get(User, user_id)
    if user:
        action = PendingAction(action_type="delete", user_id=user.id)
        session.add(action)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/promo/add")
async def admin_promo_add(
    code: str = Form(...),
    reward_days: int = Form(...),
    max_uses: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin)
):
    promo = Promocode(code=code.strip().upper(), reward_days=reward_days, max_uses=max_uses)
    session.add(promo)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/promo/delete")
async def admin_promo_delete(
    promo_id: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin)
):
    promo = await session.get(Promocode, promo_id)
    if promo:
        await session.delete(promo)
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/apply")
async def admin_apply(admin=Depends(get_current_admin)):
    async with async_session_maker() as session:
        result = await session.execute(select(PendingAction).order_by(PendingAction.created_at.asc()))
        actions = result.scalars().all()
        if not actions:
            return RedirectResponse(url="/admin", status_code=303)
        action_snapshots = []
        for action in actions:
            snapshot = {"id": action.id, "action_type": action.action_type, "user_id": action.user_id, "payload": action.payload}
            if action.user_id:
                user = await session.get(User, action.user_id)
                if user:
                    snapshot.update({"telegram_id": user.telegram_id, "vless_uuid": user.vless_uuid})
            action_snapshots.append(snapshot)
    
    outcomes: dict[int, bool] = {}
    xray = XrayManager()
    
    for action in action_snapshots:
        success = False
        try:
            action_type = action["action_type"]
            if action_type == "add":
                payload = action.get("payload") or {}
                success = await xray.add_client(email=str(payload["telegram_id"]), uuid=str(payload["vless_uuid"]))
            elif action_type in ("toggle_disable", "delete") and action.get("telegram_id"):
                success = await xray.remove_client(email=str(action["telegram_id"]))
            elif action_type == "toggle_enable" and action.get("telegram_id") and action.get("vless_uuid"):
                success = await xray.add_client(email=str(action["telegram_id"]), uuid=str(action["vless_uuid"]))
        except Exception:
            logger.exception("Failed to deliver pending action to Xray")
        outcomes[action["id"]] = success
        
    async with session_scope(async_session_maker) as session:
        for action in action_snapshots:
            if not outcomes.get(action["id"]):
                continue
            db_action = await session.get(PendingAction, action["id"])
            if not db_action:
                continue
            if action["action_type"] == "add":
                payload = action.get("payload") or {}
                user = await session.scalar(select(User).where(User.telegram_id == int(payload["telegram_id"])))
                if not user:
                    session.add(User(telegram_id=int(payload["telegram_id"]), vless_uuid=payload["vless_uuid"], is_active=True))
                else:
                    user.is_active = True
            elif action["action_type"] == "toggle_disable" and action.get("user_id"):
                user = await session.get(User, action["user_id"])
                if user:
                    user.is_active = False
            elif action["action_type"] == "toggle_enable" and action.get("user_id"):
                user = await session.get(User, action["user_id"])
                if user:
                    user.is_active = True
            elif action["action_type"] == "delete" and action.get("user_id"):
                user = await session.get(User, action["user_id"])
                if user:
                    await delete_user_with_relations(session, user)
                    continue
            await session.delete(db_action)
            
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/user/set_date")
async def admin_user_set_date(
    user_id: int = Form(...),
    end_date: str = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin)
):
    user = await session.get(User, user_id)
    if user and end_date:
        parsed_date = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        user.sub_end_date = parsed_date
        session.add(PendingAction(action_type="toggle_enable", user_id=user.id))
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/admin/pending/cancel")
async def admin_pending_cancel(
    action_id: int = Form(...),
    session: AsyncSession = Depends(get_write_session),
    admin=Depends(get_current_admin)
):
    action = await session.get(PendingAction, action_id)
    if action:
        await session.delete(action)
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/admin/logout")
async def admin_logout():
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})

@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit_dashboard(request: Request, admin=Depends(get_current_admin)):
    nodes = {
        "🇫🇮 Финляндия (Главный)": "127.0.0.1",
        "🇩🇪 Германия": settings.GERMANY_PUBLIC_IP,
        "🇳🇱 Нидерланды": settings.NETHERLANDS_PUBLIC_IP,
        "🇷🇺 Траффик РФ": settings.RUSSIA_BALANCER_IP,
    }
    async def run_command(args: list[str], timeout: int) -> str:
        return await asyncio.to_thread(subprocess.check_output, args, timeout=timeout, stderr=subprocess.STDOUT, text=True)
    async def audit_node(ip: str) -> dict[str, str]:
        try:
            if ip == "127.0.0.1":
                status = (await run_command(["systemctl", "is-active", "xray"], timeout=2)).strip()
                logs = await run_command(["journalctl", "-u", "xray", "-p", "3", "-n", "15", "--no-pager"], timeout=3)
            else:
                ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=3", "-o", "PasswordAuthentication=no", f"root@{ip}"]
                status = (await run_command([*ssh, "systemctl is-active xray"], timeout=5)).strip()
                logs = await run_command([*ssh, "journalctl -u xray -p 3 -n 15 --no-pager"], timeout=6)
            return {"status": status, "logs": logs.strip() if logs.strip() else "✅ Ошибок нет."}
        except Exception:
            return {"status": "error", "logs": "❌ Ошибка подключения."}
            
    results = dict(zip(nodes.keys(), await asyncio.gather(*(audit_node(ip) for ip in nodes.values()))))
    html = "<html><body style='background:#0f172a;color:#cbd5e1;padding:20px;font-family:sans-serif;'><a href='/admin' style='color:#3b82f6;'><- Назад</a><h1>📡 Аудит</h1>"
    for k, v in results.items():
        html += f"<h3>{escape(k)} - {v['status']}</h3><pre style='background:#1e293b;padding:10px;'>{escape(v['logs'])}</pre>"
    return HTMLResponse(content=html)
