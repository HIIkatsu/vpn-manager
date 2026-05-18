import os
import uuid
import sqlite3
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from yookassa import Configuration, Payment
from dotenv import load_dotenv

# Подгружаем наш .env
load_dotenv()

# Инициализируем ЮKass'у ключами из окружения
Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")

DB_PATH = os.getenv("DB_PATH", "/root/vpn-manager/database.db")
VPN_PRICE = os.getenv("VPN_PRICE", "100.00")

router = APIRouter()

# Ожидаем от фронта JSON вида: {"user_uuid": "строка"}
class PaymentRequest(BaseModel):
    user_uuid: str

@router.post("/api/payments/create")
async def create_vpn_payment(req: PaymentRequest):
    # 1. Проверяем в базе, живой ли вообще юзер, которому хотят купить VPN
    conn = sqlite3.connect(DB_PATH)
    user = conn.execute("SELECT uuid FROM users WHERE uuid = ?", (req.user_uuid,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден в базе данных")

    try:
        # 2. Генерируем ключ идемпотентности (защита ЮKassa от повторных списаний при сбое сети)
        idempotency_key = str(uuid.uuid4())
        
        # 3. Стучимся в API ЮKassa для создания черновика платежа
        payment = Payment.create({
            "amount": {"value": VPN_PRICE, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                # Ссылка, куда перенаправить юзера ПОСЛЕ оплаты (например, обратно в твой бот)
                "return_url": "https://t.me/твой_название_боты" 
            },
            "capture": True, # Списываем деньги сразу (без двухэтапного холдирования)
            "description": f"Продление подписки VPN для {req.user_uuid}",
            "metadata": {
                "user_uuid": req.user_uuid # Намертво зашиваем UUID в метаданные платежа!
            }
        }, idempotency_key)

        # 4. Логируем этот платеж в нашу таблицу orders со статусом pending
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO orders (id, user_uuid, amount, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (payment.id, req.user_uuid, float(VPN_PRICE), "pending", now_str)
        )
        conn.commit()
        
        # 5. Возвращаем фронтенду ссылку. Фронт должен просто открыть её юзеру в браузере / WebApp
        return {
            "payment_id": payment.id,
            "confirmation_url": payment.confirmation.confirmation_url
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка ЮKassa: {str(e)}")
    finally:
        conn.close()
