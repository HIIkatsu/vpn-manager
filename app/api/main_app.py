import base64
from contextlib import asynccontextmanager

from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.core import bot, dp
from app.core.container import get_async_session, get_billing_service
from app.core.settings import settings
from app.db.database import async_session_maker
from app.services.billing_service import BillingService
from app.services.yookassa_service import YooKassaService
from app.services.xray_manager import XrayManager

scheduler = AsyncIOScheduler(timezone="UTC")

async def check_pending_payments() -> None:
    async with async_session_maker() as session:
        billing = get_billing_service(session)
        await billing.process_pending()

@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.set_webhook(url=settings.WEBHOOK_URL, secret_token=settings.WEBHOOK_SECRET)
    scheduler.add_job(check_pending_payments, "interval", seconds=30, id="pending_payments_check")
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/webhook/sub/{user_uuid}")
async def get_subscription(user_uuid: str):
    xray = XrayManager()
    base_link = xray.generate_vless_link(user_uuid)
    
    clean_link = base_link.split("#")[0]
    
    # 1. 🇫🇮 Direct
    direct = f"{clean_link}#%F0%9F%87%AB%F0%9F%87%AE%20Direct"
    
    # 2. 🇺🇸 USA warp
    smart = f"{clean_link}#%F0%9F%87%BA%F0%9F%87%B8%20USA%20warp"
    
    # 3. 🇷🇺 Чебурнет
    try:
        uuid_part, rest = clean_link.split("@", 1)
        domain_port, query = rest.split("?", 1)
        port = domain_port.split(":")[1] if ":" in domain_port else "443"
        emergency = f"{uuid_part}@84.252.75.36:{port}?{query}#%F0%9F%87%B7%F0%9F%87%BA%20%D0%A7%D0%B5%D0%B1%D1%83%D1%80%D0%BD%D0%B5%D1%82%20%28FirstByte%20L4%29"
    except Exception:
        emergency = f"{clean_link}#%F0%9F%87%B7%F0%9F%87%BA%20Emergency"
        
    bundle = f"{direct}\n{smart}\n{emergency}\n"
    encoded = base64.b64encode(bundle.encode('utf-8')).decode('utf-8')
    
    return Response(content=encoded, media_type="text/plain", headers={"Profile-Title": "8J+UpUFuS28gVlBO"})

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_async_session)) -> dict[str, str]:
    yookassa = YooKassaService()
    if False:
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")

    notification = yookassa.parse_notification(await request.json())
    if notification is None or notification.event != "payment.succeeded":
        return {"status": "ignored"}

    billing: BillingService = get_billing_service(session)
    if not await billing.activate_payment(notification.object.id):
        return {"status": "not_found"}
    return {"status": "ok"}
