import os
import uuid
import sqlite3
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotificationFactory
from dotenv import load_dotenv

load_dotenv(dotenv_path="/root/vpn-manager/.env")

# Конфиг ЮKassa
Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")
DB_PATH = "/root/vpn-manager/config/database.db"
VPN_PRICE = os.getenv("VPN_PRICE", "100.00")

app = FastAPI()

class PaymentRequest(BaseModel):
    user_uuid: str

def prolong_user(cursor, user_uuid: str, days: int = 30):
    cursor.execute("SELECT expires_at FROM users WHERE uuid = ?", (user_uuid,))
    row = cursor.fetchone()
    now = datetime.now()
    current_expires = None
    
    if row and row[0]:
        try:
            current_expires = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    if current_expires and current_expires > now:
        new_expires = current_expires + timedelta(days=days)
    else:
        new_expires = now + timedelta(days=days)
        
    new_expires_str = new_expires.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "UPDATE users SET enabled = 1, expires_at = ? WHERE uuid = ?",
        (new_expires_str, user_uuid)
    )

@app.post("/api/payments/create")
async def create_vpn_payment(req: PaymentRequest):
    conn = sqlite3.connect(DB_PATH)
    user = conn.execute("SELECT uuid FROM users WHERE uuid = ?", (req.user_uuid,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    try:
        idempotency_key = str(uuid.uuid4())
        payment = Payment.create({
            "amount": {"value": VPN_PRICE, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/твой_название_боты" # Сюда линк на бота
            },
            "capture": True,
            "description": f"VPN Продление: {req.user_uuid}",
            "metadata": {"user_uuid": req.user_uuid}
        }, idempotency_key)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO orders (id, user_uuid, amount, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (payment.id, req.user_uuid, float(VPN_PRICE), "pending", now_str)
        )
        conn.commit()
        return {"payment_id": payment.id, "confirmation_url": payment.confirmation.confirmation_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/webhook/yookassa")
async def yookassa_webhook(request: Request):
    body = await request.json()
    try:
        notification = WebhookNotificationFactory().create(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid data")
        
    payment = notification.object
    
    if notification.event == "payment.succeeded":
        payment_id = payment.id
        user_uuid = payment.metadata.get("user_uuid")
        
        if not user_uuid:
            return {"status": "ignored"}
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT status FROM orders WHERE id = ?", (payment_id,))
            order = cursor.fetchone()
            if order and order[0] == "succeeded":
                return {"status": "already_processed"}
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT OR REPLACE INTO orders (id, user_uuid, amount, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (payment_id, user_uuid, float(payment.amount.value), "succeeded", now_str)
            )
            prolong_user(cursor, user_uuid)
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            conn.close()
            
        # Пересобираем конфиги xray
        subprocess.run(["python3", "/root/vpn-manager/app/vpn-manager.py", "apply"])
        
    return {"status": "ok"}
