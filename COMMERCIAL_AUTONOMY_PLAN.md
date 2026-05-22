[ПЛАН ДЕЙСТВИЙ]
Шаг 1 (Blocker Security, до релиза)
Включить реальную проверку webhook auth (Basic/HMAC по официальной схеме YooKassa).

Убрать заглушки DummyRateLimiter и ip_in_allowlist; подключить production rate-limit и CIDR allowlist.

Запретить старт с дефолтными ADMIN_USERNAME/PASSWORD; fail-fast при небезопасной конфигурации.

Ограничить /admin по сети (VPN/IP allowlist) на ingress уровне.

Шаг 2 (Runtime decoupling)
Разнести процессы:

api (FastAPI/Uvicorn),

bot (aiogram polling/webhook worker),

scheduler/worker (expiry, pending payments).

Убрать запуск polling/worker из FastAPI startup.

Добавить healthchecks и explicit graceful shutdown per process.

Шаг 3 (Domain boundaries)
Разбить main_app.py на роутеры: admin_router, billing_router, subscription_router, health_router.

Вынести lifecycle в app/runtime/lifespan.py.

Вынести webhook orchestration в отдельный service/facade слой (тонкий endpoint, толстый application service).

Шаг 4 (Idempotency hardening)
Определить единый “источник истины” активации: webhook-first, process_pending только как compensating job.

Ввести явную idempotency policy:

event_id хранить и проверять до side effects,

lock scope и retry policy документировать.

Сократить критическую секцию транзакции; внешние вызовы (Xray) обернуть в outbox/compensation strategy.

Шаг 5 (DB transaction policy)
Стандартизовать unit-of-work: один request/handler = одна транзакционная политика.

Ввести единый шаблон commit/rollback в application service слое.

Добавить аудит-лог изменений платежей и подписок.

Шаг 6 (Model hygiene)
Вынести PendingAction в отдельный файл модели, убрать дубли импортов.

Привести timezone policy к единой форме (aware UTC everywhere, без replace(tzinfo=None)).

Шаг 7 (Observability & SRE)
Structured logging с correlation IDs (request_id, payment_id, telegram_id).

Метрики: webhook reject reasons, payment activation latency, duplicate events, worker lag.

Alerting на “payment succeeded but activation failed”.
