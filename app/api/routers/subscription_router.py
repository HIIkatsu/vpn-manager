from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64, math, json, hmac, ipaddress, urllib.parse, hashlib, time, aiohttp
from urllib.parse import quote
from datetime import datetime, timezone
from app.db.models import User
from app.api.dependencies.common import get_read_session, get_write_session
from app.core.settings import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

def get_env(key, default=""):
    try:
        with open("/root/vpn-manager-v2/.env", "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default

def format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes == 0: return "0 B"
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][i]}"

def _build_subscription_url(request: Request, user: User) -> str:
    return f"https://{getattr(settings, 'WEBHOOK_URL_DOMAIN', request.url.hostname)}/webhook/sub/{user.vless_uuid}"

def _build_hiddify_deeplink(sub_url: str) -> str:
    return f"hiddify://install-config?url={quote(sub_url, safe='')}"

@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, os: str = "android", session: AsyncSession = Depends(get_read_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user or not user.is_active: return Response(content="", status_code=403)

    host_fin_domain = settings.WEBHOOK_URL_DOMAIN
    host_fin_ip = settings.FINLAND_PUBLIC_IP
    host_de = settings.GERMANY_PUBLIC_IP
    host_nl = settings.NETHERLANDS_PUBLIC_IP
    host_ru = settings.RUSSIA_BALANCER_IP

    pbk = settings.XRAY_REALITY_PUBLIC_KEY or getattr(settings, 'VLESS_PUBLIC_KEY', '')
    sid = settings.VLESS_SHORT_ID
    fp = "safari" if os.lower().strip() in ["ios", "mac", "apple"] else "chrome"

    def make_tcp(host, name, target_port=443, custom_sni="www.samsung.com", custom_sid=sid):
        return f"vless://{user.vless_uuid}@{host}:{target_port}?encryption=none&security=reality&type=tcp&fp={fp}&pbk={pbk}&sni={custom_sni}&sid={custom_sid}&flow=xtls-rprx-vision#{quote(name)}"

    fake = "00000000-0000-0000-0000-000000000000"
    divider = lambda text: f"vless://{fake}@127.0.0.1:80?type=tcp#{quote(text)}"

    configs = [
        divider("▼ 💎 РЕКОМЕНДУЕМ ▼"),
        make_tcp(host_ru, "🇪🇺 ✨ Умный профиль"),
        make_tcp(host_fin_ip, "🇪🇺 ⚡ Турбо-скорость", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_ru, "🇪🇺 🛡️ LTE / 4G Анти-глушилка", target_port=settings.XRAY_RU_WHITELIST_PORT, custom_sid=settings.XRAY_RU_WHITELIST_SHORT_ID, custom_sni="vk.com"),
        divider("▼ 🆘 ДЛЯ МОБИЛЬНОГО ▼"),
        make_tcp(host_fin_ip, "🇫🇮 Финляндия 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_de, "🇩🇪 Германия 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_nl, "🇳🇱 Нидерланды 2", target_port=settings.XRAY_REDIRECT_PORT),
        make_tcp(host_fin_ip, "🇬🇧 Великобритания", target_port=2083),
        divider("▼ 🌍 ДЛЯ WI-FI ▼"),
        make_tcp(host_fin_domain, "🇫🇮 Финляндия 1"),
        make_tcp(host_de, "🇩🇪 Германия 1"),
        make_tcp(host_nl, "🇳🇱 Нидерланды 1"),
        make_tcp(host_fin_domain, "🇸🇪 Швеция"),
        make_tcp(host_ru, "🇷🇺 Россия (Без VPN)", target_port=settings.XRAY_RU_CLEAN_PORT, custom_sid=settings.XRAY_RU_CLEAN_SHORT_ID, custom_sni="ya.ru"),
        divider("▼ 🚀 ДЛЯ СЕРВИСОВ ▼"),
        make_tcp(host_fin_domain, "🇺🇸 📺 YouTube 4K"),
        make_tcp(host_fin_domain, "🇺🇸 🤖 ChatGPT"),
        make_tcp(host_nl, "🇺🇸 📸 Insta / TikTok")
    ]

    if user.sub_end_date:
        now = datetime.now(timezone.utc)
        end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
        days_left = (end_date - now).days
        if 0 <= days_left <= 3:
            if days_left == 1: d_str = "ОСТАЛСЯ 1 ДЕНЬ"
            elif days_left in [2, 3]: d_str = f"ОСТАЛОСЬ {days_left} ДНЯ"
            else: d_str = "ОСТАЛОСЬ МЕНЕЕ 1 ДНЯ"
            configs.insert(0, divider(f"⚠️ {d_str} ПОДПИСКИ!"))

    sub_info = f"upload=0; download={user.traffic_total_bytes or 0}; total=1099511627776; expire={int(user.sub_end_date.timestamp()) if user.sub_end_date else 0}"
    profile_title = base64.b64encode("🚀 AnKo Smart VPN".encode("utf-8")).decode("utf-8")

    headers = {
        "Subscription-Userinfo": sub_info,
        "profile-update-interval": "12",
        "profile-title": f"base64:{profile_title}"
    }

    return Response(content=base64.b64encode("\n".join(configs).encode("utf-8")).decode("utf-8"), media_type="text/plain", headers=headers)

@router.get("/setup")
async def root_instruction(request: Request): return templates.TemplateResponse(request=request, name="setup.html")

# --- СТАТУС ОПЛАТЫ (РЕДИРЕКТ С КАССЫ) ---
@router.get("/cabinet/{uuid}/status")
async def payment_status(request: Request, uuid: str):
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Обработка платежа | AnKo VPN</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <script>
            tailwind.config = {{
              theme: {{ extend: {{ colors: {{ dark: {{ 800: '#1e293b', 900: '#0f172a' }} }} }} }}
            }}
            // Авто-редирект обратно в кабинет через 7 секунд
            setTimeout(() => {{ window.location.href = '/cabinet/{uuid}'; }}, 7000);
        </script>
    </head>
    <body class="bg-dark-900 text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="max-w-md w-full bg-dark-800 p-8 rounded-3xl border border-slate-700/50 shadow-2xl text-center relative overflow-hidden">
            <div class="mb-6 flex justify-center">
                <div class="w-20 h-20 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
            </div>
            <h2 class="text-2xl font-bold text-white mb-3">Проверяем оплату...</h2>
            <p class="text-slate-400 text-sm mb-6 leading-relaxed">
                Обычно банки обрабатывают перевод за 1-2 минуты.<br>
                Как только деньги поступят, подписка активируется автоматически.
            </p>
            <a href="/cabinet/{uuid}" class="inline-block w-full py-4 px-6 bg-slate-700/50 hover:bg-slate-700 text-white font-medium rounded-xl transition-all border border-slate-600">
                Вернуться в кабинет
            </a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# --- СТРАНИЦА ВЫБОРА ОПЛАТЫ ---
@router.get("/cabinet/{uuid}/pay/{amount}")
async def web_pay(request: Request, uuid: str, amount: float, session: AsyncSession = Depends(get_write_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user: return Response("User not found", status_code=404)
    
    days = 30 if amount == 100.0 else 90 if amount == 250.0 else 365
    amount_ym = str(int(amount))
    amount_str = f"{amount:.2f}"
    
    # Формируем URL для возврата в веб-кабинет
    base_url = f"https://{getattr(settings, 'WEBHOOK_URL_DOMAIN', request.url.hostname)}"
    return_url = f"{base_url}/cabinet/{uuid}/status"

    # 1. ЮMoney
    YOOMONEY_RECEIVER = get_env("YOOMONEY_RECEIVER", "4100119543123060")
    ym_params = {"receiver": YOOMONEY_RECEIVER, "quickpay-form": "shop", "targets": f"VPN {days} d", "sum": amount_ym, "label": f"{user.telegram_id}_{days}", "successURL": return_url}
    ym_url = f"https://yoomoney.ru/quickpay/confirm.xml?{urllib.parse.urlencode(ym_params)}"

    # 2. AnyPay (Используем urlencode для безопасной передачи return_url)
    ANYPAY_PROJECT_ID = get_env("ANYPAY_PROJECT_ID", "17784")
    ANYPAY_SECRET_KEY = get_env("ANYPAY_SECRET_KEY", "")
    anypay_pay_id = f"{user.telegram_id}{int(time.time() % 1000):03d}"
    
    sign_str = f"{ANYPAY_PROJECT_ID}:{anypay_pay_id}:{amount_str}:RUB:VPN:{return_url}:{return_url}:{ANYPAY_SECRET_KEY}"
    anypay_params = {
        "merchant_id": ANYPAY_PROJECT_ID,
        "pay_id": anypay_pay_id,
        "amount": amount_str,
        "currency": "RUB",
        "desc": "VPN",
        "success_url": return_url,
        "fail_url": return_url,
        "sign": hashlib.sha256(sign_str.encode()).hexdigest()
    }
    anypay_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(anypay_params)}"

    # 3. CryptoBot
    crypto_url = ""
    CRYPTOBOT_TOKEN = get_env("CRYPTOBOT_TOKEN", "589728:AA0etJX4eBfcwigpnGzaWdSjD2aIQ5cfqqV")
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post("https://pay.crypt.bot/api/createInvoice", headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}, json={"currency_type": "fiat", "fiat": "RUB", "amount": amount_ym, "description": f"VPN {days}d", "payload": f"{user.telegram_id}_{days}"}) as resp:
                res_data = await resp.json()
                if res_data.get("ok"): 
                    crypto_url = res_data["result"].get("pay_url", "")
                    if crypto_url.startswith("https://t.me/"):
                        crypto_url = crypto_url.replace("https://t.me/", "tg://resolve?domain=").replace("?start=", "&start=")
    except Exception: pass

    crypto_btn_html = f'''
    <a href="{crypto_url}" class="group block relative rounded-2xl bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 hover:border-blue-500/50 p-4 transition-all duration-200">
        <div class="flex items-center gap-4">
            <div class="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-400 border border-blue-500/20 shadow-[0_0_15px_rgba(59,130,246,0.15)] group-hover:scale-110 transition-transform">
                <i class="fa-brands fa-bitcoin"></i>
            </div>
            <div class="text-left">
                <div class="font-medium text-white group-hover:text-blue-400 transition-colors">Криптовалюта</div>
                <div class="text-xs text-slate-400">CryptoBot Telegram</div>
            </div>
        </div>
    </a>
    ''' if crypto_url else ''

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Оплата тарифа | AnKo VPN</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <script>
            tailwind.config = {{ theme: {{ extend: {{ colors: {{ brand: {{ 400: '#818cf8', 500: '#6366f1' }}, dark: {{ 800: '#1e293b', 900: '#0f172a' }} }} }} }} }}
        </script>
    </head>
    <body class="bg-dark-900 text-slate-200 min-h-screen flex items-center justify-center p-4 font-sans selection:bg-brand-500 selection:text-white">
        <div class="max-w-md w-full">
            <div class="bg-dark-800 rounded-3xl p-6 md:p-8 border border-slate-700/50 shadow-2xl relative overflow-hidden backdrop-blur-sm">
                <i class="fa-solid fa-wallet absolute -right-6 -top-6 text-[100px] text-slate-700/10 rotate-12"></i>
                <div class="relative z-10">
                    <div class="text-center mb-8">
                        <h2 class="text-3xl font-extrabold text-white mb-2 tracking-tight">Счет на {int(amount)} ₽</h2>
                        <p class="text-slate-400 text-sm">Выберите способ оплаты</p>
                    </div>
                    <div class="space-y-3">
                        <a href="{anypay_url}" class="group block relative rounded-2xl bg-brand-500/10 hover:bg-brand-500/20 border border-brand-500/30 hover:border-brand-500 p-4 transition-all duration-200">
                            <div class="absolute -top-2.5 right-4 bg-brand-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider shadow-[0_0_10px_rgba(99,102,241,0.5)]">Удобно</div>
                            <div class="flex items-center gap-4">
                                <div class="w-10 h-10 rounded-xl bg-brand-500/20 flex items-center justify-center text-brand-400 border border-brand-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)] group-hover:scale-110 transition-transform"><i class="fa-solid fa-bolt"></i></div>
                                <div class="text-left"><div class="font-medium text-white group-hover:text-brand-400 transition-colors">Карта РФ / СБП</div><div class="text-xs text-brand-400/80">Мгновенное зачисление</div></div>
                            </div>
                        </a>
                        <a href="{ym_url}" class="group block relative rounded-2xl bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700 hover:border-emerald-500/50 p-4 transition-all duration-200">
                            <div class="flex items-center gap-4">
                                <div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400 border border-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.1)] group-hover:scale-110 transition-transform"><i class="fa-solid fa-credit-card"></i></div>
                                <div class="text-left"><div class="font-medium text-white group-hover:text-emerald-400 transition-colors">СберPay / ЮMoney</div><div class="text-xs text-slate-400">Без комиссии</div></div>
                            </div>
                        </a>
                        {crypto_btn_html}
                    </div>
                    <a href="/cabinet/{uuid}" class="mt-8 flex items-center justify-center gap-2 w-full py-4 px-6 bg-dark-900/50 text-slate-400 hover:text-white font-medium rounded-xl transition-all border border-slate-700/50 hover:bg-slate-800/80">
                        <i class="fa-solid fa-arrow-left"></i> Вернуться назад
                    </a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@router.get("/cabinet/{uuid}")
async def web_cabinet(request: Request, uuid: str, session: AsyncSession = Depends(get_write_session)):
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    if not user: return Response(content="Профиль не найден", status_code=404)
    user.preferred_os = (request.query_params.get("os") or user.preferred_os or "android").lower()
    await session.commit()
    
    now = datetime.now(timezone.utc)
    end_date = user.sub_end_date
    if end_date and end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    days_left = (end_date - now).days if end_date else 0
    days_left = max(0, days_left)
    end_date_str = end_date.strftime("%d.%m.%Y") if end_date else "Нет данных"
    traffic_bytes = user.traffic_total_bytes or 0
    formatted_traffic = format_bytes(traffic_bytes)
    traffic_percent = min(100, round((traffic_bytes / 1099511627776) * 100, 1))
    
    return templates.TemplateResponse(request=request, name="cabinet.html", context={
        "request": request, "user": user,
        "sub_url": _build_subscription_url(request, user),
        "hiddify_deeplink": _build_hiddify_deeplink(_build_subscription_url(request, user)),
        "days_left": days_left, "end_date_str": end_date_str,
        "formatted_traffic": formatted_traffic, "traffic_percent": traffic_percent,
        "os_name": user.preferred_os
    })

def verify_sync_token(request: Request):
    auth = request.headers.get("authorization", "")
    sync_token = settings.SYNC_NODES_TOKEN.strip()
    if not sync_token or not hmac.compare_digest(auth, f"Bearer {sync_token}"):
        raise HTTPException(status_code=403, detail="Forbidden")

@router.get("/webhook/sync-nodes-777")
async def generate_nodes_config(request: Request, session: AsyncSession = Depends(get_read_session)):
    verify_sync_token(request)
    users = (await session.execute(select(User))).scalars().all()
    clients = [{"id": str(u.vless_uuid), "flow": "xtls-rprx-vision", "email": str(u.telegram_id)} for u in users if getattr(u, 'is_active', False)]
    clients.append({"id": "11111111-1111-1111-1111-111111111111", "flow": "xtls-rprx-vision", "email": "transit_node_ru"})
    
    prv, sid = settings.XRAY_REALITY_PRIVATE_KEY, settings.VLESS_SHORT_ID
    config = {
      "log": {"loglevel": "warning"},
      "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
      "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "block"}],
      "routing": {"rules": [{"inboundTag": ["api"], "outboundTag": "api", "type": "field"}]},
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": settings.XRAY_MAIN_PORT, "protocol": "vless", "tag": "vless-smart",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "www.samsung.com:443", "xver": 0, "serverNames": ["www.samsung.com"], "privateKey": prv, "shortIds": [sid]}}
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_CLEAN_PORT, "protocol": "vless", "tag": "vless-ru-clean",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "ya.ru:443", "xver": 0, "serverNames": ["ya.ru", "yandex.ru"], "privateKey": prv, "shortIds": [settings.XRAY_RU_CLEAN_SHORT_ID]}}
        },
        {
          "listen": "0.0.0.0", "port": settings.XRAY_RU_WHITELIST_PORT, "protocol": "vless", "tag": "vless-ru-whitelist",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "dest": "vk.com:443", "xver": 0, "serverNames": ["vk.com", "m.vk.com"], "privateKey": prv, "shortIds": [settings.XRAY_RU_WHITELIST_SHORT_ID]}}
        }
      ]
    }
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
      "inbounds": [
        {"listen": "0.0.0.0", "port": 10085, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}, "tag": "api"},
        {
          "listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "vless-smart-transit",
          "settings": {"clients": clients, "decryption": "none"},
          "streamSettings": {
              "network": "tcp", "security": "reality",
              "realitySettings": {
                  "show": False, "dest": "www.samsung.com:443", "xver": 0,
                  "serverNames": ["www.samsung.com"],
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
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
        },
        {
          "protocol": "vless", "tag": "eu-ger",
          "settings": {"vnext": [{"address": settings.GERMANY_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
        },
        {
          "protocol": "vless", "tag": "eu-nl",
          "settings": {"vnext": [{"address": settings.NETHERLANDS_PUBLIC_IP, "port": 443, "users": [{"id": "11111111-1111-1111-1111-111111111111", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
          "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": "www.samsung.com", "publicKey": pbk, "shortId": sid, "fingerprint": "chrome"}}
        }
      ],
      "routing": {
        "domainStrategy": "IPIfNonMatch",
        "balancers": [{"tag": "eu-balancer", "selector": ["eu-"]}],
        "rules": [
          {"inboundTag": ["api"], "outboundTag": "api", "type": "field"},
          {"inboundTag": ["vless-ru-clean"], "outboundTag": "direct", "type": "field"},
          {"type": "field", "outboundTag": "direct", "domain": ["domain:ru", "domain:su", "domain:рф", "domain:vk.com", "domain:yandex.ru", "domain:ya.ru", "domain:mail.ru", "domain:sberbank.ru", "domain:gosuslugi.ru"]},
          {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
          {"type": "field", "balancerTag": "eu-balancer", "network": "tcp,udp"}
        ]
      }
    }
    return Response(content=json.dumps(config, indent=2), media_type="application/json")
