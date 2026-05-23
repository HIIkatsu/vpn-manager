# AnKo VPN Manager (v2) — Production Audit Report

## Executive Summary

**Итог:** проект в текущем состоянии **не готов к production (NO-GO)**.

Ключевые риски:
1. Поломанный Unit of Work: `session_scope` не фиксирует транзакции.
2. Некорректный вызов webhook-аутентификации YooKassa (ошибка интерфейса).
3. Блокирующий rate limiter (SQLite) внутри async webhook-пути.
4. Неатомарный биллинг-поток: внешний side-effect (Xray) до гарантированного сохранения состояния в БД.
5. «Слепое» логирование в нескольких критичных фоновых/админских участках.

---

## Critical Findings

### C1. Unit of Work нарушен: отсутствуют `commit/rollback` в `session_scope`
- **Где:** `app/services/transaction.py`
- **Симптом:** контекст-менеджер отдаёт сессию, но не делает `commit()` при успехе и `rollback()` при ошибке.
- **Production-impact:**
  - Изменения могут не попасть в БД, особенно в путях, где разработчик рассчитывает на UoW.
  - Под нагрузкой это даст фантомные «успешные» операции с последующей рассинхронизацией бизнес-логики.
- **Реальное решение:**
  - Восстановить строгий UoW-контракт.
  - Запретить использование альтернативных способов выдачи сессий в DI, которые обходят этот контракт.
- **Патч (пример):**
  ```python
  @asynccontextmanager
  async def session_scope(session_factory):
      async with session_factory() as session:
          try:
              yield session
              await session.commit()
          except Exception:
              await session.rollback()
              raise
  ```

### C2. Webhook auth вызывается с неверными аргументами
- **Где:** `app/api/routers/billing_router.py` + `app/services/yookassa_service.py`
- **Симптом:** валидация вызывается как `is_valid_webhook_auth(request)`, хотя ожидает заголовки/тело.
- **Production-impact:**
  - Ложные 401/500 на боевых webhook’ах.
  - Деньги приняты, но подписки не активированы автоматически.
- **Реальное решение:**
  - Передавать `Authorization`, `X-...-Signature` и raw body явно.
  - Добавить интеграционный тест на реальный payload YooKassa.
- **Патч (пример):**
  ```python
  raw_body = await request.body()
  auth = request.headers.get("authorization")
  signature = request.headers.get("x-content-hmac-sha256")

  if not yookassa.is_valid_webhook_auth(auth, signature, raw_body):
      raise HTTPException(status_code=401, detail="Invalid webhook authorization")
  ```

---

## High Findings

### H1. Блокирующий SQLite rate limiter в async-контексте
- **Где:** `app/core/security.py` (`SharedRateLimiter.allow`) вызывается из async webhook.
- **Production-impact:**
  - Блокировка event loop.
  - Рост latency, деградация throughput при всплесках webhook-ов.
- **Реальное решение:**
  1. Быстрый mitigation: вынос в `asyncio.to_thread`.
  2. Правильное решение: Redis-based limiter (atomic INCR/EXPIRE или Lua).

### H2. Неатомарность биллинга: Xray side-effect до устойчивого состояния
- **Где:** `app/services/billing_service.py` (`activate_payment`).
- **Production-impact:**
  - Возможен сценарий: клиент уже активирован в Xray, а БД откатилась/не зафиксировалась.
  - Итог — рассинхрон control-plane и source-of-truth.
- **Реальное решение:**
  - Внедрить transactional outbox + worker delivery.
  - Идемпотентная обработка событий с retry и дедупликацией.

### H3. Потенциально «залипающий» статус `processing` у платежа
- **Где:** `activate_payment` ставит `processing`, затем вызывает внешние системы.
- **Production-impact:**
  - При падении процесса статус может «зависнуть».
  - Компенсация может не вернуть платеж в корректный финальный статус.
- **Реальное решение:**
  - Добавить `processing_started_at` + watchdog/compensation, который reclaims stale processing.
  - Явная state-machine (pending -> processing -> success/failed).

---

## Medium Findings

### M1. Обход единообразного UoW через альтернативный session provider
- **Где:** `app/core/container.py` (`get_async_session` через `async_session_maker()` напрямую).
- **Impact:** нарушение архитектурной инварианты «все транзакции через session_scope».
- **Решение:** оставить единственный путь выдачи сессий.

### M2. Частично неструктурированное логирование в критичных ветках
- **Где:** `admin_router`/`workers` с `print()` и проглатыванием ошибок.
- **Impact:** сложный postmortem, слабая трассировка инцидентов.
- **Решение:** использовать `logger.exception` + `log_context` (`request_id`, `payment_id`, `user_id`, `action_id`).

### M3. Watchdog-скрипт без anti-overlap гарантий
- **Где:** `watchdog.sh`.
- **Impact:** возможны гонки рестартов Xray при overlapping cron/systemd timers.
- **Решение:** `set -euo pipefail`, `flock`, backoff/jitter, ограничение частоты рестартов.

---

## Пошаговый план исправлений

## Step 1 — Блокеры релиза (срочно, до выкатки)
1. Починить `session_scope` (`commit/rollback`).
2. Привести webhook auth к корректному интерфейсу.
3. Добавить smoke/integration тест: «успешный webhook активирует подписку и отправляет уведомление».
4. Провести повторный dry-run оплаты на staging.

## Step 2 — Стабильность и производительность
1. Убрать sync SQLite из event-loop пути webhook.
2. Добавить таймауты/ретраи с метриками на внешних вызовах (YooKassa/Xray).
3. Ввести reclaim зависших `processing` платежей.

## Step 3 — Консистентность данных и side-effects
1. Внедрить transactional outbox для Xray-операций.
2. Сделать обработку событий строго идемпотентной.
3. Добавить reconciliation job: сверка БД и Xray state.

## Step 4 — Наблюдаемость и операционная готовность
1. Убрать `print`, перевести все error-paths на structured logging.
2. Гарантировать наличие correlation IDs в критичных бизнес-сценариях.
3. Завести дашборды/алерты:
   - webhook 4xx/5xx rate,
   - pending/processing backlog,
   - Xray apply failures,
   - DB pool saturation.

## Step 5 — Production readiness gate
1. Нагрузочный тест webhook + параллельные оплаты.
2. Chaos-тесты: падение воркера в середине `activate_payment`.
3. Security regression:
   - проверка trusted proxy chain,
   - тест обходов allowlist,
   - валидация secret handling.
4. Формальный Go/No-Go чеклист с владельцами и ETA.

---

## Оценка готовности к выходу на рынок

- **Сейчас:** **NO-GO**.
- **Условный GO:** после закрытия Step 1 и Step 2 + успешных тестов из Step 5.
- **Риск при запуске без исправлений:** высокий (потеря консистентности биллинга, неактивация оплаченных подписок, деградация под нагрузкой).
