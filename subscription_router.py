from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64, math, json, hmac, urllib.parse, hashlib, time, aiohttp, asyncio, logging, uuid
from urllib.parse import quote
from datetime import datetime, timezone

from app.db.models import User, Payment
from app.api.dependencies.common import get_read_session, get_write_session
from app.core.settings import settings
from app.core.security import SharedRateLimiter, client_ip_from_request, sign_subscription_token, verify_subscription_token
from app.core.container import get_billing_service
from app.bot.core import bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)
rate_limiter = SharedRateLimiter()


def _enforce_rate_limit(key: str, limit: int) -> None:
    if not rate_limiter.allow(key, limit, 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

def format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes == 0: return "0 B"
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][i]}"

def _build_subscription_url(request: Request, user: User) -> str:
    expires_at = int(time.time()) + settings.SUBSCRIPTION_TOKEN_TTL_SECONDS
    signature = sign_subscription_token(str(user.telegram_id), expires_at)
    host = getattr(settings, "WEBHOOK_URL_DOMAIN", request.url.hostname)
    return f"https://{host}/webhook/sub/{user.telegram_id}?exp={expires_at}&sig={signature}"
def _build_hiddify_deeplink(sub_url: str) -> str: return f"hiddify://install-config?url={quote(sub_url, safe='')}"

# --- ГЕНЕРАТОР ПОДПИСОК ---
@router.get("/webhook/sub/tg_free")
async def tg_free_subscription(request: Request):
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    host = settings.FINLAND_PUBLIC_IP
    pbk = getattr(settings, 'VLESS_PUBLIC_KEY', '') or settings.XRAY_REALITY_PUBLIC_KEY
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
    sid = settings.VLESS_SHORT_ID
    from urllib.parse import quote
    import base64
    
    vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#{quote('🚀 TELEGRAM')}"
    
    encoded = base64.b64encode(vless_link.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Subscription-Userinfo": "upload=0; download=0; total=1099511627776; expire=0", 
        "profile-update-interval": "12", 
        "profile-title": f"base64:{base64.b64encode('AnKo VPN (Telegram)'.encode('utf-8')).decode('utf-8')}"
    }
    return Response(content=encoded, media_type="application/octet-stream", headers=headers)

@router.get("/webhook/sub/{sub_id}")
async def get_subscription(request: Request, sub_id: str, os: str = "android", exp: int | None = None, sig: str | None = None, session: AsyncSession = Depends(get_read_session)):
    client_ip = client_ip_from_request(request)
    _enforce_rate_limit(f"subscription:{sub_id}:{client_ip}", settings.SUBSCRIPTION_RATE_LIMIT_PER_MINUTE)
    if not (exp and sig and verify_subscription_token(sub_id, exp, sig)):
        if not settings.LEGACY_SUBSCRIPTION_URLS_ENABLED:
            return Response(content="", status_code=403)
            
    if sub_id.isdigit() and len(sub_id) < 15:
        user = (await session.execute(select(User).where(User.telegram_id == int(sub_id)))).scalars().first()
    else:
        user = (await session.execute(select(User).where(User.vless_uuid == sub_id))).scalars().first()
        
    divider = lambda text: f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:80?type=tcp#{quote(text)}"
    if not user:
        expired_config = [
            divider("🔴 ПРОФИЛЬ УДАЛЕН ИЛИ НЕ НАЙДЕН"),
            divider("🔄 ОБНОВИТЕ ССЫЛКУ ИЗ БОТА")
        ]
    now_utc = datetime.now(timezone.utc)
    is_expired = not user.is_active
    if user.sub_end_date:
        end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
        if end_date <= now_utc:
            is_expired = True

    if is_expired:
        host_fin_ip = settings.FINLAND_PUBLIC_IP
        pbk = getattr(settings, 'VLESS_PUBLIC_KEY', '') or settings.XRAY_REALITY_PUBLIC_KEY
        sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
        sid = settings.VLESS_SHORT_ID
        tg_free_link = f"vless://b5a71a3c-1111-2222-3333-000000000000@{host_fin_ip}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#{quote('🚀 TELEGRAM')}"

        sub_info = f"upload=0; download={user.traffic_total_bytes or 0}; total=1099511627776; expire={int(user.sub_end_date.timestamp()) if user.sub_end_date else 0}"
        expired_config = [
            divider("🔴 ПОДПИСКА ИСТЕКЛА!"),
            tg_free_link,
            divider("🔄 ПРОДЛИТЕ В БОТЕ ИЛИ КАБИНЕТЕ")
        ]
        
        host = getattr(settings, "WEBHOOK_URL_DOMAIN", request.url.hostname)
        cabinet_url = f"https://{host}/cabinet/{user.vless_uuid}"
        
        title = "🚀 AnKo Smart VPN (ИСТЕКЛА)"
        headers = {
            "Subscription-Userinfo": sub_info, 
            "profile-update-interval": "12", 
            "profile-web-page-url": cabinet_url,
            "support-url": "tg://resolve?domain=AnKoVPN_bot",
            "profile-title": f"base64:{base64.b64encode(title.encode('utf-8')).decode('utf-8')}"
        }
        return Response(content=base64.b64encode("\n".join(expired_config).encode("utf-8")).decode("utf-8"), media_type="text/plain", headers=headers)

    host_fin_domain, host_fin_ip, host_de, host_nl, host_ru = settings.WEBHOOK_URL_DOMAIN.replace("pay.", ""), settings.FINLAND_PUBLIC_IP, settings.GERMANY_PUBLIC_IP, settings.NETHERLANDS_PUBLIC_IP, settings.RUSSIA_BALANCER_IP
    pbk, sid = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', ''), settings.VLESS_SHORT_ID
    fp = "qq"

    def make_tcp(host, name, target_port=443, custom_sni=None, custom_sid=sid):
        if not custom_sni:
            if host == host_nl:
                custom_sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
            elif host != host_ru:
                custom_sni = "wikipedia.org" if target_port == 443 else "yahoo.com"
            else:
                custom_sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
        return f"vless://{user.vless_uuid}@{host}:{target_port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni={custom_sni}&sid={custom_sid}&flow=xtls-rprx-vision#{quote(name)}"
    divider = lambda text: f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:80?type=tcp#{quote(text)}"

    configs = [
        divider("▼ 💎 РЕКОМЕНДУЕМ ▼"), 
        make_tcp(host_ru, "🇪🇺 ✨ Умный профиль", target_port=settings.XRAY_MAIN_PORT), 
        make_tcp(host_fin_ip, "🇪🇺 ⚡ Турбо-скорость", target_port=settings.XRAY_REDIRECT_PORT), 
        make_tcp(host_ru, "🇪🇺 🛡️ LTE / 4G Анти-глушилка", target_port=settings.XRAY_RU_WHITELIST_PORT, custom_sid=settings.XRAY_RU_WHITELIST_SHORT_ID, custom_sni="vk.com"),
        divider("▼ 🆘 ДЛЯ МОБИЛЬНОГО ▼"), 
        make_tcp(host_fin_ip, "🇫🇮 Финляндия 2", target_port=settings.XRAY_REDIRECT_PORT), 
        divider("▼ 🌍 ДЛЯ WI-FI ▼"), 
        make_tcp(host_fin_domain, "🇫🇮 Финляндия 1"), 
        make_tcp(host_fin_domain, "🇸🇪 Швеция"), 
        make_tcp(host_ru, "🇷🇺 Россия (Без VPN)", target_port=settings.XRAY_RU_CLEAN_PORT, custom_sid=settings.XRAY_RU_CLEAN_SHORT_ID, custom_sni="ya.ru"),
        divider("▼ 🚀 ДЛЯ СЕРВИСОВ ▼"), 
        make_tcp(host_fin_domain, "🇺🇸 📺 YouTube 4K"), 
        make_tcp(host_fin_domain, "🇺🇸 🤖 ChatGPT")
    ]
    if user.sub_end_date:
        now = datetime.now(timezone.utc)
        end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
        days_left = (end_date - now).days
        if 0 <= days_left <= 3: 
            configs = [divider(f"⚠️ ОСТАЛОСЬ {max(1, days_left)} ДН. ПРOДЛИТЕ!")] + configs

    sub_info = f"upload=0; download={user.traffic_total_bytes or 0}; total=1099511627776; expire={int(user.sub_end_date.timestamp()) if user.sub_end_date else 0}"
    
    host = getattr(settings, "WEBHOOK_URL_DOMAIN", request.url.hostname)
    cabinet_url = f"https://{host}/cabinet/{user.vless_uuid}"
    
    title = "🚀 AnKo Smart VPN"
    headers = {
        "Subscription-Userinfo": sub_info, 
        "profile-update-interval": "12", 
        "profile-web-page-url": cabinet_url,
        "support-url": "tg://resolve?domain=AnKoVPN_bot",
        "profile-title": f"base64:{base64.b64encode(title.encode('utf-8')).decode('utf-8')}"
    }
    return Response(content=base64.b64encode("\n".join(configs).encode("utf-8")).decode("utf-8"), media_type="text/plain", headers=headers)


# --- ВЕБ-ДИЗАЙН И НОВАЯ ЛОГИКА ОПЛАТЫ (ЮКАССА + ANYPAY) ---
@router.get("/setup")
async def root_instruction(request: Request): return templates.TemplateResponse(request=request, name="setup.html")

@router.get("/cabinet/{vless_uuid}/status")
async def payment_status(request: Request, vless_uuid: str, session: AsyncSession = Depends(get_write_session)):
    client_ip = client_ip_from_request(request)
    _enforce_rate_limit(f"payment_status:{telegram_id}:{client_ip}", settings.PAYMENT_STATUS_RATE_LIMIT_PER_MINUTE)
    user = await session.scalar(select(User).where(User.vless_uuid == vless_uuid))
    success = False
    
    if user:
        try:
            from app.services.yookassa_service import YooKassaService
            from sqlalchemy import desc
            billing = get_billing_service(session)
            latest_payment = (await session.execute(select(Payment).where(Payment.user_id == user.id).order_by(desc(Payment.created_at)).limit(1))).scalars().first()
            
            if latest_payment:
                if latest_payment.status == "success":
                    success = True
                elif latest_payment.status == "pending":
                    yk = YooKassaService()
                    for attempt in range(5): 
                        try:
                            remote = await yk.fetch_remote_payment(latest_payment.payment_id)
                            if remote and remote.status == "succeeded":
                                await billing.activate_payment(latest_payment.payment_id, f"web_pull_{latest_payment.payment_id}")
                                await session.commit()
                                success = True
                                try:
                                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 В личный кабинет", callback_data="menu_profile")]])
                                    await bot.send_message(chat_id=user.telegram_id, text="✅ <b>Оплата успешно получена!</b>\n\nПодписка продлена, приятного пользования!", parse_mode="HTML", reply_markup=kb)
                                except Exception:
                                    logger.exception("Failed to send YooKassa payment status notification")
                                break
                        except Exception:
                            logger.exception("Failed to refresh YooKassa payment status")
                        if success: break
                        await asyncio.sleep(2.0)
        except Exception:
            logger.exception("Payment status refresh failed")

    if success:
        icon_html = '<div class="w-24 h-24 rounded-[2rem] bg-emerald-500/10 flex items-center justify-center text-emerald-400 border border-emerald-500/20 shadow-[0_0_40px_rgba(16,185,129,0.2)] relative"><i class="fa-solid fa-check text-5xl"></i><div class="absolute inset-0 bg-emerald-400/20 blur-2xl rounded-full z-[-1]"></div></div>'
        title, desc, delay = "Оплата найдена", "Подписка успешно продлена. Бот уже прислал чек.<br>Возвращаем в кабинет...", 4000
    else:
        icon_html = '<div class="w-24 h-24 rounded-[2rem] bg-brand-500/10 flex items-center justify-center text-brand-400 border border-brand-500/20 shadow-[0_0_40px_rgba(99,102,241,0.2)] relative"><i class="fa-solid fa-circle-notch fa-spin text-5xl"></i><div class="absolute inset-0 bg-brand-400/20 blur-2xl rounded-full z-[-1]"></div></div>'
        title, desc, delay = "Проверяем оплату", "Обычно банки обрабатывают перевод за 1-2 минуты.<br>Не закрывайте страницу, статус обновится автоматически.", 8000

    html_content = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Статус | AnKo VPN</title><script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: {{ 400: '#818cf8', 500: '#6366f1' }}, dark: {{ 800: '#1e293b', 900: '#0f172a' }} }} }} }} }}; setTimeout(() => {{window.location.href = '/cabinet/{vless_uuid}';}}, {delay});</script></head><body class="bg-dark-900 text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans selection:bg-brand-500 selection:text-white"><div class="max-w-md w-full relative z-10"><div class="bg-dark-800 rounded-3xl p-8 border border-slate-700/50 shadow-2xl relative overflow-hidden backdrop-blur-sm text-center"><i class="fa-solid fa-receipt absolute -left-8 -bottom-8 text-[140px] text-slate-700/10 -rotate-12"></i><div class="relative z-10"><div class="mb-8 flex justify-center">{icon_html}</div><h2 class="text-3xl font-extrabold text-white mb-3 tracking-tight">{title}</h2><p class="text-slate-400 text-sm mb-8 leading-relaxed">{desc}</p><a href="/cabinet/{vless_uuid}" class="inline-block w-full py-4 px-6 bg-dark-900/50 hover:bg-slate-800/80 text-slate-300 hover:text-white font-medium rounded-xl transition-all border border-slate-700/50 shadow-sm">Вернуться в кабинет</a></div></div></div></body></html>"""
    return HTMLResponse(content=html_content)

@router.get("/cabinet/{vless_uuid}/pay/{amount}")
async def web_pay(request: Request, vless_uuid: str, amount: float, session: AsyncSession = Depends(get_write_session)):
    user = (await session.execute(select(User).where(User.vless_uuid == vless_uuid))).scalars().first()
    if not user: return Response("User not found", status_code=404)
    days, amount_str = (30 if amount == 100.0 else 90 if amount == 250.0 else 365), f"{amount:.2f}"
    return_url = f"https://{getattr(settings, 'WEBHOOK_URL_DOMAIN', request.url.hostname)}/cabinet/{vless_uuid}/status"

    yk_url = ""
    try:
        from app.services.yookassa_service import YooKassaService
        from app.db.repositories.payment_repo import PaymentRepository
        yk_url = await YooKassaService().create_payment(PaymentRepository(session), user.id, amount, return_url)
        await session.commit()
    except Exception:
        logger.exception("Failed to create YooKassa payment")

    anypay_url = ""
    if settings.ANYPAY_PROJECT_ID and settings.ANYPAY_SECRET_KEY:
        anypay_pay_id = f"{user.telegram_id}{int(time.time() % 1000):03d}"
        ap_params = {"merchant_id": settings.ANYPAY_PROJECT_ID, "pay_id": anypay_pay_id, "amount": amount_str, "currency": "RUB", "desc": "VPN", "success_url": return_url, "fail_url": return_url, "sign": hashlib.sha256(f"{settings.ANYPAY_PROJECT_ID}:{anypay_pay_id}:{amount_str}:RUB:VPN:{return_url}:{return_url}:{settings.ANYPAY_SECRET_KEY}".encode()).hexdigest()}
        anypay_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(ap_params)}"

    crypto_url = ""
    if settings.CRYPTOBOT_TOKEN:
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post("https://pay.crypt.bot/api/createInvoice", headers={"Crypto-Pay-API-Token": settings.CRYPTOBOT_TOKEN}, json={"currency_type": "fiat", "fiat": "RUB", "amount": str(int(amount)), "description": f"VPN {days}d", "payload": f"{user.telegram_id}_{days}"}) as resp:
                    res_data = await resp.json()
                    if res_data.get("ok"): crypto_url = res_data["result"].get("pay_url", "").replace("https://t.me/", "tg://resolve?domain=").replace("?start=", "&start=")
        except Exception:
            logger.exception("Failed to create CryptoBot invoice")

    yk_btn_html = f'''<a href="#" onclick="openPayLink('{yk_url}'); return false;" class="group block relative rounded-2xl bg-brand-500/10 hover:bg-brand-500/20 border border-brand-500/30 hover:border-brand-500 p-4 transition-all duration-200"><div class="absolute -top-2.5 right-4 bg-brand-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider shadow-[0_0_10px_rgba(99,102,241,0.5)]">Рекомендуем</div><div class="flex items-center gap-4"><div class="w-10 h-10 rounded-xl bg-brand-500/20 flex items-center justify-center text-brand-400 border border-brand-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)] group-hover:scale-110 transition-transform"><i class="fa-solid fa-credit-card"></i></div><div class="text-left"><div class="font-medium text-white group-hover:text-brand-400 transition-colors">Карта РФ / СБП (ЮKassa)</div><div class="text-xs text-brand-400/80">Официальный банковский шлюз</div></div></div></a>''' if yk_url else ''
    anypay_btn_html = f'''<a href="{anypay_url}" class="group block relative rounded-2xl bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 hover:border-emerald-500/50 p-4 transition-all duration-200"><div class="flex items-center gap-4"><div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400 border border-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.1)] group-hover:scale-110 transition-transform"><i class="fa-solid fa-rotate"></i></div><div class="text-left"><div class="font-medium text-white group-hover:text-emerald-400 transition-colors">Запасной шлюз (AnyPay)</div><div class="text-xs text-slate-400">СБП / Карты РФ</div></div></div></a>''' if anypay_url else ''
    crypto_btn_html = f'''<a href="#" onclick="openPayLink('{crypto_url}'); return false;" class="group block relative rounded-2xl bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 hover:border-blue-500/50 p-4 transition-all duration-200"><div class="flex items-center gap-4"><div class="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-400 border border-blue-500/20 shadow-[0_0_15px_rgba(59,130,246,0.15)] group-hover:scale-110 transition-transform"><i class="fa-brands fa-bitcoin"></i></div><div class="text-left"><div class="font-medium text-white group-hover:text-blue-400 transition-colors">Криптовалюта</div><div class="text-xs text-slate-400">CryptoBot Telegram</div></div></div></a>''' if crypto_url else ''

    return HTMLResponse(content=f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Оплата тарифа | AnKo VPN</title><script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><script>tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: {{ 400: '#818cf8', 500: '#6366f1' }}, dark: {{ 800: '#1e293b', 900: '#0f172a' }} }} }} }} }}</script></head><body class="bg-dark-900 text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans selection:bg-brand-500 selection:text-white"><div class="max-w-md w-full"><div class="bg-dark-800 rounded-3xl p-6 md:p-8 border border-slate-700/50 shadow-2xl relative overflow-hidden backdrop-blur-sm"><i class="fa-solid fa-wallet absolute -right-6 -top-6 text-[100px] text-slate-700/10 rotate-12"></i><div class="relative z-10"><div class="text-center mb-8"><h2 class="text-3xl font-extrabold text-white mb-2 tracking-tight">Счет на {int(amount)} ₽</h2><p class="text-slate-400 text-sm">Выберите способ оплаты</p></div><div class="space-y-3">{yk_btn_html}{crypto_btn_html}</div><a href="/cabinet/{vless_uuid}" class="mt-8 flex items-center justify-center gap-2 w-full py-4 px-6 bg-dark-900/50 text-slate-400 hover:text-white font-medium rounded-xl transition-all border border-slate-700/50 hover:bg-slate-800/80"><i class="fa-solid fa-arrow-left"></i> Отмена</a></div></div></div><script src="https://telegram.org/js/telegram-web-app.js"></script><script>function openPayLink(url) {{ if(window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openLink) {{ window.Telegram.WebApp.openLink(url); }} else {{ window.location.href = url; }} }}</script></body></html>""")


# --- DEEPLINK REDIRECT (opens in system browser, redirects to app) ---
@router.get("/deeplink")
async def deeplink_redirect(request: Request, app: str = "happ", url: str = ""):
    """Промежуточная страница для deeplink. JS на странице сам формирует правильный deeplink."""
    import json as _json
    app_safe = _json.dumps(app)
    url_safe = _json.dumps(url)
    
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Подключение AnKo VPN...</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; 
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  .card {{ text-align: center; padding: 2rem; }}
  .spinner {{ width: 40px; height: 40px; border: 4px solid #334155; border-top-color: #6366f1;
              border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 1.5rem; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  h2 {{ font-size: 1.25rem; margin-bottom: 0.5rem; }}
  p {{ color: #94a3b8; font-size: 0.875rem; margin-bottom: 1.5rem; }}
  a {{ display: inline-block; padding: 0.75rem 1.5rem; background: #6366f1; color: white;
       border-radius: 0.75rem; text-decoration: none; font-weight: 600; }}
</style>
</head>
<body>
<div class="card">
  <div class="spinner"></div>
  <h2>Открываем приложение...</h2>
  <p>Если приложение не открылось автоматически, нажмите кнопку ниже.</p>
  <a href="#" id="btn" onclick="doOpen(); return false;">Открыть вручную</a>
</div>
<script>
  var appSchemes = {{
    'happ': 'happ://import-remote-profile?url=',
    'v2raytun': 'v2raytun://import-remote-profile?url=',
    'v2box': 'v2box://import-remote-profile?url=',
    'hiddify': 'hiddify://import-remote-profile?url='
  }};
  var appName = {app_safe};
  var subUrl = {url_safe};
  var prefix = appSchemes[appName] || appSchemes['happ'];
  var deeplink = prefix + encodeURIComponent(subUrl) + '#AnKo%20Smart%20VPN';
  
  document.getElementById('btn').href = deeplink;
  
  function doOpen() {{
    window.location.href = deeplink;
  }}
  
  setTimeout(doOpen, 400);
</script>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/cabinet/{vless_uuid}")
async def web_cabinet(request: Request, vless_uuid: str, session: AsyncSession = Depends(get_write_session)):
    user = (await session.execute(select(User).where(User.vless_uuid == vless_uuid))).scalars().first()
    if not user: return Response("Профиль не найден", status_code=404)
    user.preferred_os = (request.query_params.get("os") or user.preferred_os or "android").lower()
    await session.commit()
    now = datetime.now(timezone.utc)
    end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date and user.sub_end_date.tzinfo is None else user.sub_end_date
    days_left = max(0, (end_date - now).days) if end_date else 0
    traffic_bytes = user.traffic_total_bytes or 0
    sub_url = _build_subscription_url(request, user)
    return templates.TemplateResponse(request=request, name="cabinet.html", context={"request": request, "user": user, "sub_url": sub_url, "hiddify_deeplink": _build_hiddify_deeplink(sub_url), "days_left": days_left, "end_date_str": end_date.strftime("%d.%m.%Y") if end_date else "Нет данных", "formatted_traffic": format_bytes(traffic_bytes), "traffic_percent": min(100, round((traffic_bytes / 1099511627776) * 100, 1)), "os_name": user.preferred_os})

try:
    from redis import Redis
except Exception:
    Redis = None

def get_redis_client():
    if settings.REDIS_URL and Redis:
        return Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    return None

def inject_temp_users(clients: list, routing: dict):
    static_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    static_email = "tg_temp_public"
    
    if not any(c.get("id") == static_uuid for c in clients):
        clients.append({"id": static_uuid, "flow": "xtls-rprx-vision", "email": static_email})
    
    # Inject routing rules for the static email
    routing["rules"].insert(0, {"type": "field", "user": [static_email], "domain": ["geosite:telegram", "domain:cloudflare.com", "geosite:google"], "outboundTag": "direct"})
    routing["rules"].insert(1, {"type": "field", "user": [static_email], "ip": ["geoip:telegram"], "outboundTag": "direct"})
    routing["rules"].insert(2, {"type": "field", "user": [static_email], "outboundTag": "block"})

    redis_client = get_redis_client()
    if not redis_client: return
    try:
        temp_keys = redis_client.keys("tg_temp_key:*")
        if temp_keys:
            temp_uuids = [k.split("tg_temp_key:")[1] for k in temp_keys]
            temp_emails = []
            for uid in temp_uuids:
                if uid == static_uuid: continue
                clients.append({"id": uid, "flow": "xtls-rprx-vision", "email": f"tg_temp_{uid}"})
                temp_emails.append(f"tg_temp_{uid}")
            if temp_emails:
                routing["rules"].insert(0, {"type": "field", "user": temp_emails, "domain": ["geosite:telegram", "domain:cloudflare.com", "geosite:google"], "outboundTag": "direct"})
                routing["rules"].insert(1, {"type": "field", "user": temp_emails, "ip": ["geoip:telegram"], "outboundTag": "direct"})
                routing["rules"].insert(2, {"type": "field", "user": temp_emails, "outboundTag": "block"})
    except Exception as e:
        logger.exception("Failed to inject temp users")

@router.get("/start")
async def bootstrap_page(request: Request):
    # Static UUID for all Telegram bypass users
    temp_uuid = "b5a71a3c-1111-2222-3333-000000000000"
    
    # Push the static UUID to nodes asynchronously just in case it's missing
    from app.services.node_sync import ActivePushDispatcher
    import asyncio
    asyncio.create_task(ActivePushDispatcher().add_client(telegram_id="tg_temp_public", uuid=temp_uuid))
    
    host = settings.FINLAND_PUBLIC_IP
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sni = getattr(settings, "VLESS_SNI", "www.samsung.com")
    sid = settings.VLESS_SHORT_ID
    
    vless_link = f"vless://{temp_uuid}@{host}:443?encryption=none&security=reality&type=tcp&fp=qq&pbk={pbk}&sni={sni}&sid={sid}&flow=xtls-rprx-vision#%F0%9F%94%92+Telegram+VPN"
    return templates.TemplateResponse(request=request, name="bootstrap.html", context={"vless_link": vless_link, "temp_uuid": temp_uuid})




# --- ИСПРАВЛЕННЫЕ ГЕНЕРАТОРЫ XRAY ---
def verify_sync_token(request: Request):
    auth = request.headers.get("authorization", "")
    sync_token = settings.SYNC_NODES_TOKEN.strip()
    if not sync_token or not hmac.compare_digest(auth, f"Bearer {sync_token}"): raise HTTPException(status_code=403, detail="Forbidden")

@router.get("/webhook/sync-nodes-777")
async def generate_nodes_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    verify_sync_token(request)
    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    clients.append({"id": "11111111-1111-1111-1111-111111111111", "flow": "xtls-rprx-vision", "email": "transit_node_ru"})
    
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    try: redirect_port = int(settings.XRAY_REDIRECT_PORT)
    except Exception: redirect_port = 20443

    config = {
      "log": {"loglevel": "warning"},
      "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
      "policy": {
        "levels": {
          "0": {
            "statsUserUplink": True,
            "statsUserDownlink": True
          }
        },
        "system": {
          "statsInboundUplink": True,
          "statsInboundDownlink": True
        }
      },

      "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}],
      "routing": {"rules": [{"inboundTag": ["api"], "outboundTag": "api", "type": "field"}]},
      "observatory": {"subjectSelector": ["eu-"], "probeUrl": "https://cp.cloudflare.com/generate_204", "probeInterval": "1m", "enableConcurrency": True},
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": 8443, "protocol": "vless", "tag": "vless-direct",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": f"{getattr(settings, 'VLESS_SNI', 'www.samsung.com')}:443", "xver": 0, "serverNames": [getattr(settings, "VLESS_SNI", "www.samsung.com"), "wikipedia.org", "yahoo.com"], "privateKey": prv, "shortIds": [sid]}}
        },
        {
          "listen": "0.0.0.0", "port": redirect_port, "protocol": "vless", "tag": "vless-redirect",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": f"{getattr(settings, 'VLESS_SNI', 'www.samsung.com')}:443", "xver": 0, "serverNames": [getattr(settings, "VLESS_SNI", "www.samsung.com"), "wikipedia.org", "yahoo.com"], "privateKey": prv, "shortIds": [sid]}}
        }
      ]
    }
    inject_temp_users(clients, config["routing"])
    return Response(content=json.dumps(config, indent=2), media_type="application/json")

@router.get("/webhook/sync-transit-777")
async def generate_transit_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    verify_sync_token(request)
    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')

    config = {
      "log": {"loglevel": "warning"},
      "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
      "policy": {
        "levels": {
          "0": {
            "statsUserUplink": True,
            "statsUserDownlink": True
          }
        },
        "system": {
          "statsInboundUplink": True,
          "statsInboundDownlink": True
        }
      },

      "observatory": {"subjectSelector": ["eu-"], "probeUrl": "https://cp.cloudflare.com/generate_204", "probeInterval": "1m", "enableConcurrency": True},
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "vless-smart-transit",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": f"{getattr(settings, 'VLESS_SNI', 'www.samsung.com')}:443", "xver": 0,
                  "serverNames": [getattr(settings, "VLESS_SNI", "www.samsung.com"), "wikipedia.org", "yahoo.com"],
                  "privateKey": prv, "shortIds": [sid]
              }
          }
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_CLEAN_PORT, "protocol": "vless", "tag": "vless-ru-clean",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "ya.ru:443", "xver": 0,
                  "serverNames": ["ya.ru", "yandex.ru"],
                  "privateKey": prv, "shortIds": [settings.XRAY_RU_CLEAN_SHORT_ID]
              }
          }
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_WHITELIST_PORT, "protocol": "vless", "tag": "vless-ru-whitelist",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "vk.com:443", "xver": 0,
                  "serverNames": ["vk.com", "m.vk.com"],
                  "privateKey": prv, "shortIds": [settings.XRAY_RU_WHITELIST_SHORT_ID]
              }
          }
        }
      ],
      "outbounds": [
        {"protocol": "freedom", "tag": "direct"},
        {"protocol": "blackhole", "tag": "block"},
        {
          "protocol": "vless", "tag": "eu-fin",
          "settings": {"vnext": [{"address": settings.FINLAND_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": getattr(settings, "VLESS_SNI", "www.samsung.com"), "publicKey": pbk, "shortId": sid, "fingerprint": "qq"}}
        },
        {
          "protocol": "vless", "tag": "eu-ger",
          "settings": {"vnext": [{"address": settings.GERMANY_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "wikipedia.org", "publicKey": pbk, "shortId": sid, "fingerprint": "qq"}}
        },
        {
          "protocol": "vless", "tag": "eu-nl",
          "settings": {"vnext": [{"address": settings.NETHERLANDS_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "yahoo.com", "publicKey": pbk, "shortId": sid, "fingerprint": "qq"}}
        }
      ],
      "routing": {
        "domainStrategy": "IPIfNonMatch",
        "balancers": [{"tag": "eu-balancer", "selector": ["eu-"], "strategy": {"type": "leastPing"}}],
        "rules": [
          {"inboundTag": ["api"], "outboundTag": "api", "type": "field"},
          {"inboundTag": ["vless-ru-clean"], "outboundTag": "direct", "type": "field"},
          {"type": "field", "outboundTag": "direct", "domain": ["domain:ru", "domain:su", "domain:рф", "domain:vk.com", "domain:yandex.ru", "domain:ya.ru", "domain:mail.ru", "domain:sberbank.ru", "domain:gosuslugi.ru"]},
          {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
          {"type": "field", "balancerTag": "eu-balancer", "network": "tcp,udp"}
        ]
      }
    }
    inject_temp_users(clients, config["routing"])
    return Response(content=json.dumps(config, indent=2), media_type="application/json")
