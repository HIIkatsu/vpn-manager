# Billing Idempotency Contract

## Scope

Документ фиксирует контракт идемпотентной обработки платежей в контуре `payment.succeeded` и компенсационного воркера pending-платежей.

## Activation Source Priority

1. **Primary source:** webhook `payment.succeeded` (`/webhook/yookassa`).
2. **Compensation source:** `BillingService.process_pending()` как fallback-механизм для pending-платежей.

`process_pending()` не должен конкурировать с webhook в "горячем" окне сразу после создания платежа. Для этого воркер обрабатывает только платежи старше 1 минуты и ограничивает batch размер.

## Idempotency Key

- Идемпотентность для операции активации основана на `processed_event_id` в таблице `payments`.
- Формат event key: `<event_type>:<payment_id>`.
- Повторная доставка того же события возвращает `{"status": "duplicate"}` без повторной бизнес-обработки.

## Concurrency Rules

- Перед активацией берется row-lock по `payment_id` (`SELECT ... FOR UPDATE`).
- Если статус `success` — операция считается уже завершенной.
- Если статус `processing` — конкурентная обработка должна выйти с retry-поведеним без двойной активации.

## Validation Rules Before Activation

Webhook-обработчик обязан валидировать до вызова активации:
- auth/signature,
- allowlist/rate-limit,
- amount + currency,
- metadata.user_id,
- `paid == true`.

## Telemetry and Diagnostics

- Для конфликтов и дублей webhook пишутся структурированные log entries с `payment_id`, `event_id`, `source`.
- Для неуспешной компенсации pending-платежей пишется warning с `source=process_pending`.

## Non-goals

- Документ не изменяет бизнес-логику тарификации/длительности подписок.
- Документ не вводит новую схему статусов за пределами текущей модели `pending/processing/success`.
