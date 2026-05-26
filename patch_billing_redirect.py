import os
import re

# 1. Патчим YooKassaService
yoo_file = "app/services/yookassa_service.py"
with open(yoo_file, "r") as f: content = f.read()
content = re.sub(
    r'def create_payment\(self, payments: PaymentRepository, user_id: int, amount: float\) -> str:',
    r'def create_payment(self, payments: PaymentRepository, user_id: int, amount: float, return_url: str = "tg://resolve?domain=NeuroVPN_AI_bot") -> str:',
    content
)
content = re.sub(
    r'"return_url": "tg://resolve\?domain=[^"]+"',
    r'"return_url": return_url',
    content
)
with open(yoo_file, "w") as f: f.write(content)

# 2. Патчим BillingService
bill_file = "app/services/billing_service.py"
if os.path.exists(bill_file):
    with open(bill_file, "r") as f: content = f.read()
    content = re.sub(
        r'def create_subscription_payment\(self, user_id: int, amount: float\) -> str:',
        r'def create_subscription_payment(self, user_id: int, amount: float, return_url: str = "tg://resolve?domain=NeuroVPN_AI_bot") -> str:',
        content
    )
    content = re.sub(
        r'create_payment\(self\.payments, user_id, amount\)',
        r'create_payment(self.payments, user_id, amount, return_url)',
        content
    )
    with open(bill_file, "w") as f: f.write(content)

# 3. Патчим Роутер
router_file = "app/api/routers/subscription_router.py"
with open(router_file, "r") as f: content = f.read()
content = re.sub(
    r'create_subscription_payment\(user_id=user\.id,\s*amount=amount\)',
    r'create_subscription_payment(user_id=user.id, amount=amount, return_url=f"https://{settings.WEBHOOK_URL_DOMAIN}/cabinet/{uuid}?payment=success")',
    content
)
with open(router_file, "w") as f: f.write(content)

print("✅ Биллинг отвязан от Telegram!")
