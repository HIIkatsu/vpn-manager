from fastapi import APIRouter, Depends, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import base64
import json
from app.db.models import User
from app.api.dependencies.common import get_async_session

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/webhook/sub/{uuid}")
async def get_subscription(uuid: str, session: AsyncSession = Depends(get_async_session)):
    # Ищем юзера напрямую через сессию, чтобы избежать багов UserService
    result = await session.execute(select(User).where(User.vless_uuid == uuid))
    user = result.scalars().first()
    
    if not user or not user.is_active:
        return Response(content="", status_code=403)

    config = {
        "dns": {"servers": ["8.8.8.8", "1.1.1.1", "localhost"]},
        "inbounds": [
            {
                "tag": "socks",
                "port": 10808,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
            {
                "tag": "http",
                "port": 10809,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
        ],
        "outbounds": [
            {
                "tag": "Echelon-1-TCP",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "neurosmmai.ru",
                            "port": 443,
                            "users": [{"id": user.vless_uuid, "encryption": "none", "flow": "xtls-rprx-vision"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": "www.samsung.com",
                        "publicKey": "sCPQc_KdGUR4T4CGYAmZj27asF8SZ32_S_o0nh-IjmI",
                        "shortId": "45b6b57266629592",
                        "fingerprint": "chrome",
                    },
                },
            },
            {
                "tag": "Echelon-2-WS",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "neurosmmai.ru",
                            "port": 443,
                            "users": [{"id": user.vless_uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {"serverName": "neurosmmai.ru", "fingerprint": "chrome"},
                    "wsSettings": {"path": "/api-v3-telemetry", "headers": {"Host": "neurosmmai.ru"}},
                },
            },
            {
                "tag": "Echelon-3-CF",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "cdn.neurosmmai.ru",
                            "port": 443,
                            "users": [{"id": user.vless_uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {"serverName": "cdn.neurosmmai.ru", "fingerprint": "chrome"},
                    "wsSettings": {"path": "/api-v3-telemetry", "headers": {"Host": "cdn.neurosmmai.ru"}},
                },
            },
            {
                "tag": "Echelon-4-xHTTP",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "neurosmmai.ru",
                            "port": 443,
                            "users": [{"id": user.vless_uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {"serverName": "neurosmmai.ru", "fingerprint": "chrome"},
                    "xhttpSettings": {"path": "/api-v3-telemetry", "mode": "auto"},
                },
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "domain": [
                        "domain:ru",
                        "domain:su",
                        "domain:рф",
                        "geosite:category-ru",
                        "domain:yandex.com",
                        "domain:yandex.net",
                        "domain:vk.com",
                        "domain:ozon.ru",
                        "domain:ozonusercontent.com",
                        "domain:ozon-st.com",
                        "domain:wildberries.ru",
                        "domain:wb.ru",
                        "domain:sberbank.ru",
                    ],
                },
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
                {"type": "field", "network": "tcp,udp", "balancerTag": "Smart_Switch"},
            ],
            "balancers": [
                {
                    "tag": "Smart_Switch",
                    "selector": ["Echelon-"],
                    "strategy": {
                        "type": "leastLoad",
                        "settings": {
                            "maxRTT": "1500ms",
                            "expected": 1,
                            "baselines": ["150ms", "400ms", "600ms"],
                            "tolerance": 0.2,
                        },
                    },
                    "fallbackTag": "Echelon-2-WS",
                }
            ],
        },
        "burstObservatory": {
            "pingConfig": {
                "timeout": "3s",
                "interval": "30s",
                "sampling": 3,
                "destination": "http://www.gstatic.com/generate_204",
            },
            "subjectSelector": ["Echelon-"],
        },
    }

    raw_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    b64_link = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

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
