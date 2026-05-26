import os, re
base = "/root/vpn-manager-v2"
yoo_file = os.path.join(base, "app/services/yookassa_service.py")
bill_file = os.path.join(base, "app/services/billing_service.py")
router_file = os.path.join(base, "app/api/routers/subscription_router.py")

# Патчинг... (логика та же, пути надежные)
for f_path in [yoo_file, bill_file]:
    if os.path.exists(f_path):
        with open(f_path, "r") as f: c = f.read()
        c = re.sub(r'def create_payment\(self, payments: PaymentRepository, user_id: int, amount: float\) -> str:', r'def create_payment(self, payments: PaymentRepository, user_id: int, amount: float, return_url: str = "tg://resolve?domain=NeuroVPN_AI_bot") -> str:', c)
        c = re.sub(r'"return_url": "tg://resolve\?domain=[^"]+"', r'"return_url": return_url', c)
        with open(f_path, "w") as f: f.write(c)

with open(router_file, "r") as f: c = f.read()
c = re.sub(r'create_subscription_payment\(user_id=user\.id,\s*amount=amount\)', r'create_subscription_payment(user_id=user.id, amount=amount, return_url=f"https://{settings.WEBHOOK_URL_DOMAIN}/cabinet/{uuid}?payment=success")', c)
with open(router_file, "w") as f: f.write(c)
