# План разработки VPN Manager v2

## Этап 1: Базовый скелет и БД
- [ ] 1.1. Инициализировать проект: создать структуру папок `app/api`, `app/bot`, `app/db`, `app/core`, `app/services`, `app/static`, `app/templates`.
- [ ] 1.2. Настроить `app/core/config.py` (через `pydantic-settings` для чтения `.env`).
- [ ] 1.3. Поднять асинхронное подключение к PostgreSQL в `app/db/database.py` и настроить генератор сессий (`get_async_session`).
- [ ] 1.4. Написать SQLAlchemy-модели: `User` (telegram_id, vless_uuid, is_active, sub_end_date) и `Payment` (payment_id, amount, status).
- [ ] 1.5. Настроить Alembic, сгенерировать `env.py` для асинхронной работы и создать первую миграцию.

## Этап 2: Ядро, Веб и Telegram-бот
- [ ] 2.1. Создать базовое приложение FastAPI в `main.py`. Добавить роут `GET /health` для Docker healthcheck.
- [ ] 2.2. Настроить маунт `StaticFiles` для `app/static` и подключить `Jinja2Templates` для рендера стартовой страницы.
- [ ] 2.3. Инициализировать aiogram 3. Создать эндпоинт FastAPI (`POST /webhook/telegram`) для приема апдейтов бота.
- [ ] 2.4. Написать базовые хэндлеры бота: `/start`, проверка профиля, кнопка "Продлить подписку". Вынести их в `app/bot/handlers.py`.
- [ ] 2.5. Привязать хэндлеры к БД (регистрация нового юзера, генерация уникального vless_uuid).

## Этап 3: Биллинг (ЮKassa)
- [ ] 3.1. Реализовать асинхронный сервис `app/services/yookassa_srv.py` для генерации ссылок на оплату.
- [ ] 3.2. Добавить эндпоинт `POST /api/webhook/yookassa` для приема статусов платежей от ЮКассы.
- [ ] 3.3. Реализовать логику продления: при статусе "succeeded" обновлять `sub_end_date` юзера в БД и отправлять ему уведомление через экземпляр aiogram bot.

## Этап 4: Подписки (Генерация конфигов Hiddify)
- [ ] 4.1. Создать роут `GET /vpn-{secret_prefix}/{user_uuid}`.
- [ ] 4.2. Написать сервис генерации конфига: если юзер активен, формировать правильный JSON/Base64 строку конфигурации VLESS.
- [ ] 4.3. Настроить отдачу конфига с правильными HTTP-заголовками (`profile-title`, `profile-update-interval`, `subscription-userinfo`) напрямую из FastAPI.

## Этап 5: Интеграция с Xray
- [ ] 5.1. Написать интерфейс/сервис в `app/services/xray_srv.py` для добавления и удаления UUID в памяти Xray (через gRPC API).
- [ ] 5.2. Интегрировать вызовы Xray в логику покупки (добавление UUID) и истечения подписки (удаление U
- [ ] UID).
