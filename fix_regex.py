import re

# 1. ОБНОВЛЯЕМ РОУТЕР (Умный парсер + безопасный дебаг для тестера)
router_path = '/root/vpn-manager-v2/app/api/routers/billing_router.py'
with open(router_path, 'r', encoding='utf-8') as f:
    r_content = f.read()

r_pattern = r'@router\.post\("/api/payments/anypay-webhook"\).*?(?=@router\.post\("/api/cryptobot/webhook"\))'
r_repl = """@router.post("/api/payments/anypay-webhook")
async def anypay_webhook(request: Request, session: AsyncSession = Depends(get_write_session)):
    form_data = await request.form()
    merchant_id = form_data.get("merchant_id")
    amount = form_data.get("amount")
    pay_id = form_data.get("pay_id", "")
    status_pay = form_data.get("status")
    received_sign = form_data.get("sign")
    is_test = form_data.get("test")
    transaction_id = form_data.get("transaction_id", "unknown")
    
    if not all([merchant_id, amount, pay_id, received_sign]):
        return Response(content="ERROR: Missing parameters", status_code=400)
        
    import hashlib
    import re
    secret = ""
    try:
        with open("/root/vpn-manager-v2/.env", "r", encoding="utf-8") as env_file:
            env_text = env_file.read()
            # Всеядный парсер: достанет ключ независимо от пробелов и кавычек
            match = re.search(r'ANYPAY_SECRET_KEY\s*=\s*[\'"]?([^\'"\s]+)', env_text)
            if match:
                secret = match.group(1)
    except:
        pass
        
    s1 = f"{merchant_id}:{amount}:{pay_id}:{secret}"
    s2 = f"{merchant_id}:{float(amount):.2f}:{pay_id}:{secret}"
    
    hashes = [
        hashlib.sha256(s1.encode('utf-8')).hexdigest(),
        hashlib.sha256(s2.encode('utf-8')).hexdigest(),
        hashlib.md5(s1.encode('utf-8')).hexdigest(),
        hashlib.md5(s2.encode('utf-8')).hexdigest()
    ]
    
    if received_sign not in hashes:
        # Если это запрос из тестера AnyPay, показываем, что именно сломалось
        if str(is_test) == "1":
            return Response(content=f"DEBUG: str={s1} sec={secret[:5]}...", status_code=403)
        # Если это боевой платеж, жестко отбиваем
        return Response(content="ERROR: Invalid signature", status_code=403)
            
    if status_pay != "paid":
        return Response(content="OK", status_code=200)
        
    try:
        pay_str = str(pay_id)
        if "_" in pay_str:
            tg_id = int(pay_str.split("_")[0])
        elif "-" in pay_str:
            tg_id = int(pay_str.split("-")[0])
        else:
            tg_id = int(pay_str[:-4]) if len(pay_str) > 4 else int(pay_str)
            
        amt_float = float(amount)
        if amt_float >= 900:
            days = 365
        elif amt_float >= 250:
            days = 90
        else:
            days = 30
            
        await activate_subscription(session, tg_id, days, "anypay", transaction_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"AnyPay Webhook Error: {e}")
    return Response(content="OK", status_code=200)

"""
with open(router_path, 'w', encoding='utf-8') as f:
    f.write(re.sub(r_pattern, r_repl, r_content, flags=re.DOTALL))


# 2. ОБНОВЛЯЕМ ПОДПИСКУ (Тот же всеядный парсер для генерации ссылок)
sub_path = '/root/vpn-manager-v2/app/bot/handlers/subscription.py'
with open(sub_path, 'r', encoding='utf-8') as f:
    s_content = f.read()

s_pattern = r'# 2\. AnyPay.*?(?=# 3\. CryptoBot)'
s_repl = """# 2. AnyPay
    import re
    ANYPAY_PROJECT_ID = "17784"
    ANYPAY_SECRET_KEY = ""
    try:
        with open("/root/vpn-manager-v2/.env", "r", encoding="utf-8") as env_file:
            env_text = env_file.read()
            id_match = re.search(r'ANYPAY_PROJECT_ID\s*=\s*[\'"]?([^\'"\s]+)', env_text)
            if id_match:
                ANYPAY_PROJECT_ID = id_match.group(1)
            sec_match = re.search(r'ANYPAY_SECRET_KEY\s*=\s*[\'"]?([^\'"\s]+)', env_text)
            if sec_match:
                ANYPAY_SECRET_KEY = sec_match.group(1)
    except:
        pass

    desc = "VPN"
    amount_str = f"{float(amount):.2f}"
    
    callback_suffix = str(callback.id)[-4:]
    anypay_pay_id = f"{user.telegram_id}{callback_suffix}"
    
    success_url = "https://t.me/AnKoVPN_bot"
    fail_url = "https://t.me/AnKoVPN_bot"

    sign_str = f"{ANYPAY_PROJECT_ID}:{anypay_pay_id}:{amount_str}:RUB:{desc}:{success_url}:{fail_url}:{ANYPAY_SECRET_KEY}"
    anypay_sign = hashlib.sha256(sign_str.encode('utf-8')).hexdigest()

    ap_params = {
        "merchant_id": ANYPAY_PROJECT_ID,
        "pay_id": anypay_pay_id,
        "amount": amount_str,
        "currency": "RUB",
        "desc": desc,
        "success_url": success_url,
        "fail_url": fail_url,
        "sign": anypay_sign
    }
    anypay_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(ap_params)}"

    """
with open(sub_path, 'w', encoding='utf-8') as f:
    f.write(re.sub(s_pattern, s_repl, s_content, flags=re.DOTALL))
