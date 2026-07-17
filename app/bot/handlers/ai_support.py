import asyncio
import logging
from typing import Any
import math
from datetime import datetime, timezone
import time

import httpx
from aiogram import F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.enums import ParseMode

from app.core.settings import settings
from app.services.user_service import UserService

router = Router()
logger = logging.getLogger(__name__)

MISTRAL_API_KEY = "Pt5cSnGPKJGzqEXFbZfyAtu5Mq5xuDRl"
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "mistral-medium-2505"

BASE_SYSTEM_PROMPT = """Ты — умный и дружелюбный ИИ-ассистент сервиса "AnKoVPN".
Твоя главная специализация — помощь с нашим VPN (настройки, оплата, решение проблем).

ТВОЙ ХАРАКТЕР И ОБЩЕНИЕ:
1. Ты ведешь себя естественно и живо.
2. СТРОГИЙ ЗАПРЕТ НА ПОСТОРОННИЕ ЗАДАЧИ: НИКОГДА не пиши программный код, не решай математику, не пиши сочинения.
3. СТРОГИЙ ЗАПРЕТ НА ОБЕЩАНИЯ: Ты НЕ МОЖЕШЬ самостоятельно перевыпустить ключ, вернуть деньги, отменить подписку или добавить новую локацию. Если просят это сделать — скажи, что у тебя нет таких прав, и направь их нажимать нужные кнопки (например, [BTN_SOS] для перевыпуска ключа) или к живому оператору [BTN_SUPPORT].
4. Если пользователь просто общается не по теме, ответь одним предложением и переведи тему на VPN.
5. Не будь навязчивым.

ИНФОРМАЦИЯ О СЕРВИСЕ "AnKoVPN":
- Умный профиль и страны: В сервисе есть ручной выбор локаций и "Умный профиль". Умный профиль автоматически балансирует нагрузку: он открывает российские сайты напрямую, а заблокированные — через быстрые зарубежные серверы. Всегда рекомендуй использовать именно Умный профиль как самое удобное решение, но знай, что выбор локаций тоже доступен. СТРОГИЙ ЗАПРЕТ: не называй конкретные страны (не пиши про Францию, США и т.д.), просто говори "доступны различные локации на выбор".
- Телефоны: V2RayNG (Android), V2Box / Streisand (iOS).
- Компьютеры: Любой V2Ray-клиент (Windows / MacOS / Linux).
- Тарифы: 1 месяц = 100 ₽, 3 месяца = 250 ₽, 1 год = 900 ₽.
- У нас есть удобный ВЕБ-КАБИНЕТ (он же Личный кабинет). Там можно смотреть статистику, настраивать подключение в 1 клик и оплачивать подписку.

ТЕГИ ДЛЯ КНОПОК (ИСПОЛЬЗОВАТЬ ПО КОНТЕКСТУ):
СТРОГИЙ ЗАПРЕТ: НИКОГДА не генерируй гиперссылки (URL-адреса) в тексте. Не выдумывай сайты (например, ankovpn.com/pay). В боте нет никаких скрытых команд (типа /pay или /status), кроме /start и /profile. Не выдумывай их!
Ты можешь вызывать интерфейсные кнопки, добавив спецтег в конец сообщения. Выбирай те кнопки, которые лучше всего подходят по смыслу (можно сразу несколько):
- [BTN_CABINET] — Если юзер хочет посмотреть статистику в веб-кабинете, либо если просит "личный кабинет" с красивым интерфейсом.
- [BTN_CONNECT] — Если юзер спрашивает, как подключить VPN или просит дать ему настройки.
- [BTN_SOS] — Если юзер жалуется, что VPN не работает или просит перевыпустить/сбросить ключ.
- [BTN_PROFILE] — Если юзер спрашивает про оплату, продление подписки или свой ID.
- [BTN_SUPPORT] — Если у пользователя сложная техническая проблема и нужен живой оператор.
В обычных диалогах (не о VPN) кнопки ВООБЩЕ НЕ НУЖНЫ. Не вставляй их просто так! Не выдумывай другие теги.

ФОРМАТИРОВАНИЕ (CRITICAL):
Telegram использует строгий HTML-парсинг. НИКОГДА не используй Markdown (никаких **звездочек** или #).
Используй только: <b>жирный текст</b>, <i>курсивный текст</i>, <code>код</code>. Разбивай длинный текст на абзацы пустыми строками.

ДАННЫЕ ПОЛЬЗОВАТЕЛЯ В ЧАТЕ:
{user_context}
"""

def format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes == 0: return "0 B"
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][i]}"

# Кэш истории сообщений (user_id -> {'last_active': timestamp, 'history': []})
user_chat_history = {}
last_history_cleanup = time.time()
HISTORY_TTL_SECONDS = 180  # 3 минуты
MAX_HISTORY_MESSAGES = 4   # 2 последние пары вопрос-ответ

async def call_mistral(user_id: int, user_message: str, user_context: str) -> str:
    global last_history_cleanup
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = BASE_SYSTEM_PROMPT.replace("{user_context}", user_context)
    
    now = time.time()
    if now - last_history_cleanup > 60:
        expired = [uid for uid, data in user_chat_history.items() if now - data['last_active'] > HISTORY_TTL_SECONDS]
        for uid in expired:
            user_chat_history.pop(uid, None)
        last_history_cleanup = now

    if user_id in user_chat_history:
        user_data = user_chat_history[user_id]
        if now - user_data['last_active'] > HISTORY_TTL_SECONDS:
            user_data['history'] = []
    else:
        user_chat_history[user_id] = {'last_active': now, 'history': []}
        
    history = user_chat_history[user_id]['history']
    
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    
    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(MISTRAL_API_URL, headers=headers, json=payload)
                if response.status_code == 429:
                    logger.warning("Mistral API 429 Too Many Requests. Retrying...")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                
                response.raise_for_status()
                data = response.json()
                assistant_response = data["choices"][0]["message"]["content"]
                
                # Сохраняем в историю очищенный от тегов кнопок текст, чтобы не путать ИИ в будущих запросах
                clean_response = assistant_response
                for tag in ["[BTN_CABINET]", "[BTN_CONNECT]", "[BTN_SOS]", "[BTN_PROFILE]", "[BTN_SUPPORT]"]:
                    clean_response = clean_response.replace(tag, "")
                    
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": clean_response.strip()})
                if len(history) > MAX_HISTORY_MESSAGES:
                    history = history[-MAX_HISTORY_MESSAGES:]
                user_chat_history[user_id]['history'] = history
                user_chat_history[user_id]['last_active'] = time.time()
                
                return assistant_response
        except httpx.HTTPStatusError as e:
            logger.error(f"Mistral API HTTP Error: {e.response.status_code} - {e.response.text}")
            if e.response.status_code >= 500:
                await asyncio.sleep(1)
                continue
            break
        except Exception as e:
            logger.error(f"Mistral API Request Error: {e}")
            await asyncio.sleep(1)
            continue
            
    return "К сожалению, сейчас линия поддержки перегружена. Пожалуйста, попробуйте задать вопрос чуть позже."

@router.message(F.text)
async def ai_support_handler(message: Message, user_service: UserService) -> None:
    text = message.text.strip()
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Получаем контекст пользователя
    user = await user_service.get_by_telegram_id(message.from_user.id)
    if not user:
        return
        
    now = datetime.now(timezone.utc)
    if not user.sub_end_date:
        has_access = True
    else:
        end_date = user.sub_end_date.replace(tzinfo=timezone.utc) if user.sub_end_date.tzinfo is None else user.sub_end_date
        days_expired = (now - end_date).days
        has_access = days_expired <= 14
        
    if not has_access:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="menu_profile")]
        ])
        await message.answer("🤖 ИИ-ассистент доступен только при активной подписке (или не более 2-х недель после окончания).", reply_markup=keyboard)
        return

    if user.is_active and not user.sub_end_date:
        date_str = "Безлимитная (Навсегда)"
        is_active_text = "Активна 🟢"
        days_left_text = "Бесконечно"
    elif user.sub_end_date:
        now = datetime.now(timezone.utc)
        end_date = user.sub_end_date
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        
        if now > end_date:
            is_active_text = "Истекла 🔴"
            date_str = end_date.strftime("%d.%m.%Y (уже прошла)")
            days_left_text = "0"
        else:
            is_active_text = "Активна 🟢" if user.is_active else "Приостановлена 🔴"
            date_str = end_date.strftime("%d.%m.%Y")
            days_left_text = str((end_date - now).days)
    else:
        is_active_text = "Нет подписки ⚪"
        date_str = "Не оформлена"
        days_left_text = "0"
            
    traffic = format_bytes(user.traffic_total_bytes or 0)
    
    user_context = (
        f"ID Telegram: {user.telegram_id}\n"
        f"Статус подписки: {is_active_text}\n"
        f"Осталось дней: {days_left_text} (до {date_str})\n"
        f"Использовано трафика: {traffic} из 1 TB\n"
    )

    response_text = await call_mistral(message.from_user.id, text, user_context)
    
    # Очищаем Markdown звездочки, так как мы требуем от ИИ HTML
    response_text = response_text.replace("**", "<b>").replace("<b> ", " <b>")
    
    # Закрываем парные b-теги, если ИИ написал **текст**
    # Но надежнее просто удалить решетки
    response_text = response_text.replace("### ", "<b>").replace("## ", "<b>").replace("# ", "<b>")
    
    # Simple fix for mistral sometimes doing bold with ** instead of <b> despite rules:
    # Mistral might output: **bold text**
    parts = response_text.split("**")
    if len(parts) > 1 and len(parts) % 2 == 1:
        # We have balanced pairs of **
        for i in range(1, len(parts), 2):
            parts[i] = f"<b>{parts[i]}</b>"
        response_text = "".join(parts)
    else:
        response_text = response_text.replace("**", "")

    # Парсим теги для кнопок
    inline_keyboard = []
    
    if "[BTN_CABINET]" in response_text:
        response_text = response_text.replace("[BTN_CABINET]", "")
        if user and user.telegram_id:
            os_name = getattr(user, "preferred_os", "android")
            cabinet_url = f"https://{settings.WEBHOOK_URL_DOMAIN}/cabinet/{user.vless_uuid}?os={os_name}"
            inline_keyboard.append([InlineKeyboardButton(text="🌐 Открыть веб-кабинет", web_app=WebAppInfo(url=cabinet_url))])
            
    tags_mapping = {
        "[BTN_CONNECT]": InlineKeyboardButton(text="🚀 Подключить VPN", callback_data="menu_connect"),
        "[BTN_SOS]": InlineKeyboardButton(text="🆘 Не работает VPN", callback_data="menu_sos"),
        "[BTN_PROFILE]": InlineKeyboardButton(text="👤 Мой профиль / Оплата", callback_data="menu_profile"),
        "[BTN_SUPPORT]": InlineKeyboardButton(text="💬 Написать живому оператору", url="https://t.me/ankovpn_support_bot"),
    }
    
    for tag, btn in tags_mapping.items():
        if tag in response_text:
            response_text = response_text.replace(tag, "")
            inline_keyboard.append([btn])
            
    response_text = response_text.strip()
    reply_markup = InlineKeyboardMarkup(inline_keyboard=inline_keyboard) if inline_keyboard else None

    try:
        if reply_markup:
            await message.answer(response_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        else:
            await message.answer(response_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Failed to send HTML message, falling back to plain text: {e}")
        # Если HTML поломан (незакрытые теги), отправляем чистый текст
        clean_text = response_text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
        if reply_markup:
            await message.answer(clean_text, reply_markup=reply_markup)
        else:
            await message.answer(clean_text)
