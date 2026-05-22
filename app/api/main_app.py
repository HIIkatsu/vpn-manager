from app.services.billing_service import BillingService
from app.core.container import get_billing_service
import asyncio
from decimal import Decimal
from uuid import uuid4
import secrets
import ipaddress
import json
import logging

from fastapi import FastAPI, Depends, Request, Response, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.core import bot
from app.core.settings import settings
from app.core.security import InMemoryRateLimiter, ip_in_allowlist
from app.db.database import async_session_maker
from app.db.models import User, PendingAction

from app.services.user_service import UserService
from app.services.xray_manager import XrayManager
from app.services.yookassa_service import YooKassaService

from app.bot.core import bot

# --- ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ ---
app = FastAPI(title="AnKo VPN API")
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()
logger = logging.getLogger(__name__)

# Возвращаем "мозги" боту

async def get_async_session():
    async with async_session_maker() as session:
        yield session

rate_limiter = InMemoryRateLimiter()

# --- СТАРТ API ПРОЦЕССА ---
@app.on_event("startup")
async def startup_event():
    if settings.ADMIN_USERNAME == "admin" or settings.ADMIN_PASSWORD == "admin":
        raise RuntimeError("Unsafe default admin credentials are forbidden")
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        raise RuntimeError("Admin credentials must not be empty")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_current_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} TB"

async def get_dynamic_sub_info(local_vars) -> str:
    try:
        user = next((v for v in local_vars.values() if hasattr(v, 'telegram_id') and hasattr(v, 'sub_end_date')), None)
        if not user: return "upload=0; download=0; total=1099511627776; expire=0"
        xray = XrayManager()
        stats = await xray.get_live_traffic_stats()
        used = stats.get(str(user.telegram_id), 0)
        exp = int(user.sub_end_date.timestamp()) if user.sub_end_date else 0
        return f"upload=0; download={used}; total=1099511627776; expire={exp}"
    except Exception:
        return "upload=0; download=0; total=1099511627776; expire=0"

# --- ЭНДПОИНТЫ ПОДПИСОК ---
@app.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, session: AsyncSession = Depends(get_async_session)):
    user_service = UserService(session)
    user = await user_service.get_by_uuid(uuid, session=session)
    if not user or not user.is_active:
        return Response(content="", status_code=403)
    xray = XrayManager()
    link = xray.generate_vless_link(user.vless_uuid)
    b64_link = __import__('base64').b64encode(link.encode('utf-8')).decode('utf-8')
    sub_info = await get_dynamic_sub_info(locals())
    return Response(
        content=b64_link,
        media_type="text/plain",
        headers={"Subscription-Userinfo": sub_info}
    )

# --- АДМИНКА ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, q: str = None, username: str = Depends(get_current_admin), session: AsyncSession = Depends(get_async_session)):
    xray = XrayManager()
    stats_task = asyncio.create_task(xray.get_live_traffic_stats())
    stmt = select(User).order_by(User.telegram_id)
    if q:
        stmt = stmt.where(or_(User.telegram_id.like(f"%{q}%"), User.vless_uuid.like(f"%{q}%")))
    users_task = asyncio.create_task(session.execute(stmt))
    pending_task = asyncio.create_task(session.execute(
        select(PendingAction).options(joinedload(PendingAction.user))
    ))
    live_stats = await stats_task
    users_db = (await users_task).scalars().all()
    pending_actions = (await pending_task).scalars().all()
    total_bytes = 0
    users_data = []
    for u in users_db:
        used_bytes = live_stats.get(str(u.telegram_id), 0)
        total_bytes += used_bytes
        users_data.append({
            "id": u.id,
            "telegram_id": u.telegram_id,
            "vless_uuid": u.vless_uuid,
            "is_active": u.is_active,
            "sub_end_date": u.sub_end_date.strftime("%d.%m.%Y") if u.sub_end_date else "—",
            "traffic": format_bytes(used_bytes),
            "traffic_percent": round(min(100.0, (used_bytes / (1024**4)) * 100), 2)
        })
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
            "total_traffic": format_bytes(total_bytes),
            "traffic_percent": round(min(100.0, (total_bytes / (1024**4)) * 100), 2),
            "pending": pending_data,
            "query": q or ""
        }
    )

@app.post("/admin/user/add")
async def admin_user_add(telegram_id: str = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)):
    action = PendingAction(action_type="add", payload={"telegram_id": telegram_id, "vless_uuid": str(uuid4())})
    session.add(action)
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/user/toggle")
async def admin_user_toggle(user_id: int = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)):
    user = await session.get(User, user_id)
    if user:
        user.is_active = not user.is_active
        action_type = "toggle_enable" if user.is_active else "toggle_disable"
        action = PendingAction(action_type=action_type, user_id=user.id)
        session.add(action)
        await session.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/user/delete")
async def admin_user_delete(user_id: int = Form(...), session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)):
    user = await session.get(User, user_id)
    if user:
        action = PendingAction(action_type="delete", user_id=user.id)
        session.add(action)
        await session.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/apply")
async def admin_apply(session: AsyncSession = Depends(get_async_session), admin=Depends(get_current_admin)):
    result = await session.execute(select(PendingAction))
    actions = result.scalars().all()
    if not actions: return RedirectResponse(url="/admin", status_code=303)
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
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin/logout")
async def admin_logout():
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})

# --- ВЕБХУК ЮКАССЫ ---
@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_async_session)) -> dict:
    yookassa = YooKassaService()
    authorization = request.headers.get("authorization")
    signature = request.headers.get("x-yookassa-signature")
    raw_body = await request.body()
    if not yookassa.is_valid_webhook_auth(authorization, signature, raw_body):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook authorization")

    trusted_proxies = {
        ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()
    }
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid client IP")

    if not rate_limiter.allow(f"yk:{client_ip}", settings.YOOKASSA_RATE_LIMIT_PER_MINUTE, 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    allowlist = [x.strip() for x in settings.YOOKASSA_WEBHOOK_IP_ALLOWLIST.split(",") if x.strip()]
    if not ip_in_allowlist(client_ip, allowlist):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden IP")

    notification = yookassa.parse_notification(json.loads(raw_body.decode("utf-8")))
    
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}
        
    payment_obj = notification.object
    event_id = getattr(notification, "event", "") + ":" + payment_obj.id
    
    billing: BillingService = get_billing_service(session)
    payment = await billing.payments.get_by_payment_id_for_update(payment_obj.id)
    
    if payment is None:
        logger.warning("Webhook payment not found", extra={"payment_id": payment_obj.id, "source": "webhook"})
        return {"status": "not_found"}
    if payment.processed_event_id == event_id:
        logger.info("Duplicate payment event received", extra={"event_id": event_id, "payment_id": payment_obj.id, "source": "webhook"})
        return {"status": "duplicate"}
    if payment.amount != Decimal(payment_obj.amount.value) or payment_obj.amount.currency != "RUB":
        logger.warning("Payment amount validation failed", extra={"payment_id": payment_obj.id, "event_id": event_id, "source": "webhook"})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount mismatch")
    if str(payment.user_id) != str(payment_obj.metadata.get("user_id")) or payment_obj.paid is not True:
        logger.warning("Payment metadata validation failed", extra={"payment_id": payment_obj.id, "event_id": event_id, "source": "webhook"})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Metadata mismatch")
        
    if not await billing.activate_payment(payment_obj.id, event_id):
        logger.warning("Payment activation returned retry", extra={"payment_id": payment_obj.id, "event_id": event_id, "source": "webhook"})
        return {"status": "retry"}
        
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Перейти в личный кабинет", callback_data="open_profile")]
        ])
        user = await session.get(User, payment.user_id)
        if user:
            period_text = 'на 1 год' if float(payment.amount) == 900.0 else 'на 3 месяца' if float(payment.amount) == 250.0 else 'на 1 месяц'
            await bot.send_message(
                chat_id=user.telegram_id,
                text=f"✅ <b>Оплата успешно получена!</b>\nВы оформили/продлили подписку <b>{period_text}</b>.",
                parse_mode="HTML",
                reply_markup=keyboard
            )
    except Exception as e:
        print(f"Failed to send message: {e}")
        
    return {"status": "ok"}

# --- ИНСТРУКЦИЯ ДЛЯ КЛИЕНТОВ ---
@app.get("/setup")
async def root_instruction(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html")
