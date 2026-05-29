import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from app.api.dependencies.common import get_read_session, get_write_session, get_current_admin
from app.api.utils.subscription import format_bytes
from app.core.logging_utils import log_context
from app.db.models import PendingAction, User
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
        stmt = stmt.where(cast(User.telegram_id, String).like(f"%{q.strip()}%"))

    
    

    live_stats = await stats_task
    users_db = (await session.execute(stmt)).scalars().all()
    pending_actions = (await session.execute(select(PendingAction).options(joinedload(PendingAction.user)))).scalars().all()

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
            logger.exception(
                "Failed to deliver pending action to Xray",
                extra=log_context(
                    action_source="admin_apply",
                    event_id=str(action["id"]),
                    telegram_id=action.get("telegram_id") or action.get("user_id"),
                ),
            )
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
        from datetime import datetime, timezone
        # Парсим дату из HTML5 инпута (YYYY-MM-DD) и делаем её UTC-aware
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
    import subprocess
    nodes = {
        "🇫🇮 Финляндия (Главный)": "127.0.0.1",
        "🇩🇪 Германия": "132.243.194.119",
        "🇳🇱 Нидерланды": "194.50.94.177",
        "🇷🇺 Транзит (РФ)": "132.243.230.173"
    }
    
    results = {}
    for name, ip in nodes.items():
        try:
            if ip == "127.0.0.1":
                status = subprocess.check_output(["systemctl", "is-active", "xray"], timeout=2).decode().strip()
                logs = subprocess.check_output(["journalctl", "-u", "xray", "-p", "3", "-n", "15", "--no-pager"], timeout=3).decode()
            else:
                cmd_status = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=2 -o PasswordAuthentication=no root@{ip} 'systemctl is-active xray'"
                status = subprocess.check_output(cmd_status, shell=True).decode().strip()
                
                cmd_logs = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 -o PasswordAuthentication=no root@{ip} 'journalctl -u xray -p 3 -n 15 --no-pager'"
                logs = subprocess.check_output(cmd_logs, shell=True).decode()
                
            results[name] = {"status": status, "logs": logs.strip() if logs.strip() else "✅ Ошибок нет. Xray работает штатно."}
        except subprocess.TimeoutExpired:
            results[name] = {"status": "timeout", "logs": "⚠️ Сервер не ответил вовремя. Возможно, завис."}
        except subprocess.CalledProcessError as e:
            results[name] = {"status": "error", "logs": f"❌ Ошибка подключения. Код: {e.returncode}"}

    html = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Аудит Сети | AnKo VPN</title>
        <style>
            body { background-color: #0f172a; color: #cbd5e1; font-family: system-ui, -apple-system, sans-serif; padding: 20px; margin: 0; }
            h1 { color: #f8fafc; font-size: 24px; margin-bottom: 20px; }
            .grid { display: grid; grid-template-columns: 1fr; gap: 20px; }
            .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
            .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 10px; margin-bottom: 10px; }
            .title { font-size: 18px; font-weight: bold; color: #f8fafc; }
            .badge { padding: 4px 10px; border-radius: 20px; font-size: 14px; font-weight: bold; text-transform: uppercase; }
            .bg-active { background: #10b981; color: #022c22; }
            .bg-error { background: #ef4444; color: #450a0a; }
            .bg-timeout { background: #f59e0b; color: #451a03; }
            pre { background: #0f172a; padding: 15px; border-radius: 8px; overflow-x: auto; font-family: monospace; font-size: 13px; color: #94a3b8; white-space: pre-wrap; word-break: break-all; }
            .btn-back { display: inline-block; background: #3b82f6; color: white; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: bold; margin-bottom: 20px; }
            .btn-back:hover { background: #2563eb; }
        </style>
    </head>
    <body>
        <a href="/admin" class="btn-back">← Назад в Админку</a>
        <h1>📡 Состояние серверов и логи ошибок</h1>
        <div class="grid">
    """
    for name, data in results.items():
        st = data['status']
        badge_class = "bg-active" if st == "active" else ("bg-timeout" if st == "timeout" else "bg-error")
        display_status = "РАБОТАЕТ" if st == "active" else ("ТАЙМАУТ" if st == "timeout" else "ОШИБКА")
        html += f'<div class="card"><div class="header"><div class="title">{name}</div><div class="badge {badge_class}">{display_status}</div></div><pre>{data["logs"]}</pre></div>'
        
    html += "</div></body></html>"
    return HTMLResponse(content=html)
