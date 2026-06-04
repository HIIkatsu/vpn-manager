# AnKo VPN Manager — единый production-аудит, стратегия и план идеального решения

Дата актуализации: 2026-06-04.

Этот документ заменяет прежние разрозненные markdown-отчёты и является единой точкой правды по состоянию проекта, найденным рискам, идеальной целевой архитектуре, продуктовой стратегии и фазам вывода сервиса на рынок.

---

## 0. Executive summary

**Текущая оценка готовности к рынку: 4.5/10.**

Проект уже похож на рабочий MVP: есть FastAPI backend, Telegram-бот, личный кабинет, оплаты, пробный период, генерация подписок, Xray gRPC-интеграция, outbox для части Xray-операций и базовая админка. Но для публичного запуска и особенно для популярного сервиса проект пока не готов.

Главные причины:

1. Есть критические security-риски: webhook-проверки платежей неполные, в репозитории был legacy bot token, есть публичные bearer-ссылки подписок без подписи и срока жизни.
2. В коде много инфраструктурного hardcode: IP, домены, порты, short IDs, пути до `.env`, Telegram-ссылки, SNI и fallback-порты.
3. Архитектура пока похожа на single-main-server control plane, хотя фактически управляет распределённой VPN-сетью из нескольких Xray-нод.
4. Idempotency и race-condition protection есть частично, но не сквозные: платежи, промокоды, referrals, pending admin actions, outbox и expiry flow могут расходиться.
5. Масштабирование backend-а не подготовлено: один Uvicorn process, нет обязательного Redis в deploy, нет явного DB pool tuning, admin dashboard делает O(N) и N+1 операции.
6. Traffic accounting не готов для multi-node: локальный сбор статистики с `reset=True` не даёт достоверной картины по всем нодам.
7. Продуктово сервис рискует выглядеть как “ещё один VPN”, если не упаковать его вокруг простоты, Telegram-first experience, пробного периода, понятных режимов, семейных ключей и доверия.

**Главный вывод:** проект не нужно переписывать с нуля, но перед масштабированием нужен hardening. Идеальная траектория: сначала безопасность и консистентность, затем node registry и distributed control plane, затем продуктовая упаковка, рефералка, device keys, observability и нагрузочные тесты.

---

## 1. Текущая продуктовая основа

У проекта уже есть сильные элементы, которые нужно усилить, а не выбрасывать:

- Telegram-first onboarding: старт, выбор ОС, профиль, подписка, поддержка и уведомления живут в боте.
- 3-дневный trial для новых пользователей.
- Личный кабинет с подпиской, оплатой и one-click import в Hiddify.
- Генерация нескольких профилей под сценарии: smart, turbo, mobile, Wi-Fi, service-specific profiles.
- Базовая реферальная логика: приглашённый пользователь может быть связан с referrer, а после оплаты referrer получает бонусные дни.
- Админка для ручного управления пользователями, промокодами и pending actions.

Эти элементы дают правильное направление: **не просто VPN-конфиг, а удобный Telegram-first сервис для обычного пользователя**.

---

## 2. Продуктовое позиционирование

### 2.1. Что не должно быть главной уникальностью

Не стоит строить главный оффер вокруг идей:

- “VPN сам себя чинит” — звучит так, будто сервис заранее будет ломаться.
- “Работает у вашего оператора” — пользователь часто не знает оператора, а домашний и мобильный интернет могут быть разными.
- “Нас невозможно заблокировать” — слишком сильное обещание, которое разрушит доверие при первом массовом сбое.
- “У нас много стран” — крупные VPN всегда победят количеством локаций.
- “Самый быстрый VPN” — сложно доказать, легко потерять при нагрузке.

### 2.2. Рекомендуемое позиционирование

Основная идея:

> **AnKo VPN — простой VPN в Telegram: 3 дня бесплатно, подключение в один клик, готовые режимы для YouTube, ChatGPT, мобильного интернета и Wi-Fi.**

Почему это сильнее:

- пользователь понимает, что ему не нужно разбираться в технических терминах;
- Telegram-first путь отличается от больших VPN с сайтами, email-аккаунтами и отдельными приложениями;
- trial снижает страх оплаты;
- “готовые режимы” понятнее, чем список стран и портов;
- поддержка и управление в Telegram воспринимаются как живой сервис, а не просто продажа ключа.

### 2.3. Продуктовые фишки, которые реально стоит развивать

1. **Telegram-first VPN**
   - регистрация, trial, подключение, оплата, поддержка, уведомления и управление — в Telegram;
   - минимум внешних кабинетов и сложных инструкций.

2. **3 дня бесплатно без привязки карты**
   - “сначала проверь — потом плати”;
   - основной аргумент для рекламы и блогеров.

3. **One-click подключение + QR-код**
   - Hiddify deeplink уже есть;
   - добавить QR-код для мобильного подключения;
   - сделать инструкции под iOS, Android, Windows, macOS.

4. **Готовые режимы вместо технических профилей**
   - `🚀 Быстрый режим`;
   - `📱 Мобильный режим`;
   - `🏠 Домашний Wi‑Fi`;
   - `🎬 YouTube / Видео`;
   - `🤖 ChatGPT / AI`;
   - `🛡 Резервный режим`.

5. **Device keys и семейные тарифы**
   - не один общий UUID на всё, а отдельные ключи для устройств;
   - Solo / Duo / Family / Team;
   - возможность отключить потерянное устройство;
   - отдельная статистика по устройствам.

6. **Видимая рефералка**
   - “друг получает trial, ты получаешь дни после его оплаты”;
   - экран “мои приглашения”;
   - промокоды блогеров и партнёрская аналитика.

7. **Доверие и прозрачность**
   - human-readable privacy page;
   - что логируется / что не логируется;
   - как удалить аккаунт;
   - как перевыпустить ключ;
   - status/changelog канал.

---

## 3. Оценка ёмкости 4 серверов по 1 Gbit/s

Raw bandwidth: **4 Gbit/s**.

Практическая полезная VPN-пропускная способность с учётом overhead, TCP/TLS/Reality, retransmits, imbalance и CPU обычно будет ниже:

- оптимистично: **2.8–3.6 Gbit/s**;
- если РФ-сервер в основном балансирует/транзитит, а выход идёт через 3 EU-ноды: **2.1–2.7 Gbit/s**.

Оценка одновременных активных пользователей:

| Среднее потребление активного пользователя | 4 Gbit/s ideal | 3.2 Gbit/s practical | 2.4 Gbit/s conservative |
|---:|---:|---:|---:|
| 1 Mbit/s | ~4000 | ~3200 | ~2400 |
| 2 Mbit/s | ~2000 | ~1600 | ~1200 |
| 5 Mbit/s | ~800 | ~640 | ~480 |
| 10 Mbit/s | ~400 | ~320 | ~240 |
| 25 Mbit/s | ~160 | ~128 | ~96 |
| 50 Mbit/s | ~80 | ~64 | ~48 |

Пример paid-user capacity:

- 5% пользователей активны одновременно, среднее активное потребление 5 Mbit/s, practical 3.2 Gbit/s → около **12 800 платящих пользователей**;
- 10% активны одновременно, 5 Mbit/s → около **6 400 платящих пользователей**;
- 10% активны одновременно, 10 Mbit/s → около **3 200 платящих пользователей**.

Важно: backend и control plane могут упереться раньше bandwidth, если не исправить security, stats, outbox, DB pool, Redis и admin bottlenecks.

---

## 4. Найденные проблемы и идеальное решение

### 4.1. Security и платежи

#### Проблема: YooKassa webhook не применяет auth helpers

В сервисе есть функции проверки Basic auth / webhook secret, но webhook route обрабатывает payload без обязательной проверки этих механизмов. Также route отвечает 200 даже при ошибках, что скрывает инциденты.

**Риск:** поддельные payment events, ложные активации, revenue loss, плохая расследуемость.

**Идеальное решение:**

- требовать Basic auth или отдельный webhook secret;
- делать remote verification платежа по payment_id перед активацией;
- использовать replay guard после базовой валидации;
- логировать rejected attempts;
- вернуть 401/403 для невалидных webhook-ов;
- весь provider flow проводить через единый BillingService.

#### Проблема: AnyPay/CryptoBot secrets читаются из hardcoded path

В runtime-коде есть чтение `/root/vpn-manager-v2/.env` и defaults для merchant settings.

**Риск:** silent payment failure, пустые секреты, неправильные подписи, привязка к одному серверному пути.

**Идеальное решение:**

- перенести `ANYPAY_PROJECT_ID`, `ANYPAY_SECRET_KEY`, `CRYPTOBOT_TOKEN`, payment return URLs, support links в `Settings`;
- валидировать обязательные provider settings на старте;
- удалить все прямые чтения `.env` из handlers;
- покрыть payment signature generation тестами.

#### Проблема: legacy bot token был в репозитории

Файл старого bot stub содержал Telegram token.

**Риск:** compromised bot, unsafe repo sharing, token leakage through history.

**Идеальное решение:**

- немедленно revoke token у BotFather;
- удалить файл из рабочей ветки;
- считать token скомпрометированным даже после удаления из HEAD;
- добавить secret scanning в CI;
- не хранить миграционные bot stubs с реальными токенами в repo.

#### Проблема: subscription URL — вечный bearer credential

Подписка отдаётся по UUID. Если ссылка утекла, доступ сохраняется до ручной ротации UUID.

**Риск:** шаринг подписок, невозможность точечного отзыва, abuse.

**Идеальное решение:**

- подписанные subscription URLs с HMAC и TTL;
- отдельные device keys вместо одного UUID на пользователя;
- кнопка “перевыпустить ключ”;
- rate limit на subscription endpoint;
- audit log suspicious subscription pulls.

---

### 4.2. Hardcode и конфигурация

#### Проблема: IP, ports, SNI, short IDs и links разбросаны по коду

Часть параметров находится в Settings, но реальные IP, порты, short IDs, SNI-домены, service links и dummy UUID всё ещё появляются в handlers/config generation.

**Риск:** drift между подписками и Xray config, ошибки при переносе серверов, невозможность staging/prod separation.

**Идеальное решение:**

- создать единый `NodeRegistry` / `NODES` config;
- описывать каждую ноду структурно: id, country, role, host, public_ip, ports, reality settings, weights, enabled, health;
- генерировать subscription profiles, Xray sync JSON, admin audit и status page из одной модели;
- запретить hardcoded production IP/ports/domains static check-ом в CI.

---

### 4.3. Xray, node sync и distributed control plane

#### Проблема: sync endpoints возвращают критичный Xray config

Sync endpoints отдают полный Xray JSON, включая Reality private key и clients. Даже при Bearer token это high-value endpoint.

**Риск:** компрометация всей VPN-сети при утечке токена или неправильном reverse-proxy/IP allowlist.

**Идеальное решение:**

- Bearer token + IP allowlist + trusted proxy validation;
- отдельные per-node tokens;
- короткий token rotation playbook;
- минимизация выдаваемых секретов;
- audit logs по каждому sync pull;
- подпись config payload;
- mTLS или WireGuard management network для нод в идеале.

#### Проблема: Xray operations не имеют per-node delivery state

Outbox сейчас в основном доставляет add_client локальному Xray через gRPC. Для нескольких нод нужно знать, куда конкретно доставлена операция.

**Риск:** DB считает пользователя активным, а часть нод не знает клиента; или Xray содержит orphan clients.

**Идеальное решение:**

- outbox_events + outbox_deliveries per node;
- статус доставки по каждой ноде: pending/processing/succeeded/failed;
- retry/backoff отдельно по нодам;
- reconciliation job DB ↔ Xray actual clients;
- tombstones для удалений, чтобы не терять источник правды до подтверждённого remove.

---

### 4.4. Billing, idempotency и race conditions

#### Проблема: несколько путей активации платежа

Есть прямой helper для активации подписки и отдельный BillingService. Provider flows не полностью унифицированы.

**Риск:** двойное начисление, разные правила расчёта дней, сложная поддержка инцидентов.

**Идеальное решение:**

- единая state machine платежа: created/pending/processing/succeeded/failed/refunded;
- все providers вызывают один BillingService;
- stable idempotency key на provider transaction;
- remote verification перед success;
- outbox enqueue внутри той же DB transaction;
- Telegram notification после commit.

#### Проблема: referral reward dedup key содержит timestamp

Dedup key с timestamp может позволить повторные reward-события при повторной обработке.

**Риск:** многократное начисление рефереру.

**Идеальное решение:**

- stable key: `referral_reward:{referred_user_id}:{payment_id}`;
- отдельная таблица referral_rewards с unique constraint;
- начисление только после первого успешного платежа invited user.

#### Проблема: promocode activation race condition

Промокод проверяется и инкрементируется без row-level lock; user_promocode не имеет unique constraint `(telegram_id, promocode_id)`.

**Риск:** превышение max_uses, повторное использование одним пользователем.

**Идеальное решение:**

- unique constraint `(telegram_id, promocode_id)`;
- `SELECT ... FOR UPDATE` на promocode или atomic update `used_count < max_uses`;
- сервисный метод `PromocodeService.activate()`;
- tests for concurrent activation.

---

### 4.5. Traffic accounting

#### Проблема: stats collector локальный и использует reset=True

Worker собирает live stats с локального Xray и сбрасывает counters. При нескольких collectors или нескольких нодах данные могут теряться.

**Риск:** неверная статистика, невозможность quota, некорректная аналитика нагрузки.

**Идеальное решение:**

- per-node stats collectors;
- distributed lock на `traffic_collect:{node_id}`;
- хранить deltas по `(node_id, user/device_id, time_bucket)`;
- только один collector имеет право reset конкретной ноды;
- reconciliation с Xray stats;
- admin dashboard читает агрегаты, а не live gRPC на каждый request.

---

### 4.6. Runtime, scaling и deploy

#### Проблема: single-process API

API запускается одним Uvicorn process-ом. Для production этого мало.

**Риск:** низкая отказоустойчивость, event-loop stalls, плохой throughput.

**Идеальное решение:**

- Gunicorn + Uvicorn workers или несколько systemd instances;
- Nginx upstream с health checks;
- separate bot/worker processes;
- graceful shutdown;
- readiness/liveness endpoints.

#### Проблема: Redis не является обязательной production-зависимостью

В коде есть memory fallback для rate limiter, replay guard и distributed lock, но memory fallback не распределён между процессами.

**Риск:** multi-worker deployment сломает lock/rate/replay guarantees.

**Идеальное решение:**

- Redis обязателен для production;
- fail startup в production без Redis;
- использовать Redis для rate limits, replay guard, distributed locks, lightweight queues;
- мониторить Redis latency/errors.

#### Проблема: DB engine без explicit pool tuning

Нет pool_size, max_overflow, pool_timeout, recycle, pre_ping, statement timeout.

**Риск:** connection exhaustion, hanging queries, cascading latency.

**Идеальное решение:**

- настроить async SQLAlchemy pool параметры через env;
- включить pool_pre_ping;
- statement_timeout на Postgres role/session;
- индексы для hot queries;
- slow query logs.

---

### 4.7. Admin и observability

#### Проблема: admin dashboard O(N) и N+1

Админка загружает всех пользователей и внутри loop делает дополнительные обращения за traffic totals; live stats читаются на каждый запрос.

**Риск:** admin page может положить backend при тысячах пользователей.

**Идеальное решение:**

- pagination;
- SQL aggregates;
- background cached metrics;
- отдельная metrics page;
- rate limit admin heavy actions.

#### Проблема: `/admin/audit` запускает SSH/systemctl/journalctl из web request

Remote root SSH с `StrictHostKeyChecking=no` и subprocess в web handler — плохая практика.

**Риск:** MITM, event-loop/thread exhaustion, большой blast radius.

**Идеальное решение:**

- убрать root SSH из приложения;
- node agent или Prometheus exporters;
- background health job;
- строгие known_hosts;
- показывать в admin только уже собранные статусы.

#### Проблема: много silent exceptions

`except Exception: pass` скрывает Telegram, payment, env, status и notification failures.

**Риск:** инциденты не видны до жалоб пользователей.

**Идеальное решение:**

- заменить на structured logging;
- logger.exception для критичных flows;
- metrics counters for payment/webhook/xray/telegram failures;
- Sentry или аналог для backend exceptions.

---

### 4.8. Subscription profiles и UX подключения

#### Проблема: профили выглядят как набор стран/портов, а не понятные режимы

Сейчас пользователь видит много профилей с локациями. Это может путать.

**Риск:** больше поддержки, ниже конверсия, пользователь не понимает, какой профиль выбрать.

**Идеальное решение:**

- 4–6 основных режимов в начале подписки;
- названия по задачам: fast/mobile/home/video/AI/reserve;
- страны оставить ниже как advanced;
- QR-code и one-click import;
- OS-specific instructions;
- profile ordering based on health and enabled flags.

---

## 5. Идеальная целевая архитектура

### 5.1. Control plane

- FastAPI behind Nginx.
- 2–4 API workers на main host.
- Postgres с tuned pool и регулярными migrations.
- Redis обязателен для production locks/rate/replay.
- Bot, API и workers разнесены по процессам.
- Secrets только через env/secret manager.

### 5.2. Node registry

Единый источник правды:

```yaml
nodes:
  - id: finland-main
    role: exit
    country: FI
    public_host: example.com
    public_ip: 0.0.0.0
    enabled: true
    weight: 100
    ports:
      reality_main: 443
      reality_redirect: 20443
    reality:
      sni: www.example.com
      short_id: env:XRAY_FIN_SHORT_ID
      public_key: env:XRAY_FIN_PUBLIC_KEY
```

Из этой модели генерируются:

- subscription profiles;
- Xray sync config;
- admin status;
- traffic collectors;
- load-balancing weights;
- health checks.

### 5.3. Billing

- Один BillingService для YooKassa, AnyPay, CryptoBot.
- Provider-specific webhook validators.
- Remote payment verification.
- Stable idempotency keys.
- Payment reconciliation job.
- Notifications after DB commit.

### 5.4. Access model

- Account → devices → keys.
- У каждого устройства отдельный UUID.
- Device revoke/rotate.
- Family/team limits.
- Signed subscription links per device.

### 5.5. Xray operations

- Durable outbox.
- Per-node delivery rows.
- Retry/backoff.
- Reconciliation job.
- Tombstones for deletes.
- No direct DB deletion before confirmed external removal.

### 5.6. Traffic stats

- Per-node collectors.
- Redis/advisory lock per node.
- Time-bucketed deltas.
- Aggregates for admin/cabinet.
- Alerts for abnormal traffic and collector lag.

### 5.7. Observability

Metrics:

- API latency / error rate;
- DB pool usage;
- payment webhook accepted/rejected;
- payment activation lag;
- outbox lag and failures;
- Xray gRPC errors;
- Telegram send failures;
- per-node bandwidth;
- subscription pulls;
- trial → paid conversion;
- referral conversion.

---

## 6. Фазы реализации

### Фаза 0 — cleanup и безопасность репозитория

Цель: убрать мусор и секреты, собрать документацию в одном месте.

Сделать:

1. Удалить legacy/one-off файлы, которые не используются runtime-ом и опасны для поддержки.
2. Удалить старые markdown-отчёты, оставить единый `rules.md`.
3. Revoke leaked Telegram token из истории.
4. Включить secret scanning.
5. Проверить, что удалённые файлы нигде не импортируются.

Статус в этом изменении:

- удалены старые docs markdown;
- удалены одноразовые/опасные скрипты и state-файлы;
- единый отчёт перенесён в `rules.md`.

### Фаза 1 — critical security hotfix перед любой публичной рекламой

Цель: исключить прямые security/revenue-loss риски.

Сделать:

1. Enforce YooKassa webhook auth + remote verification.
2. Перенести AnyPay/CryptoBot settings в `Settings`.
3. Удалить hardcoded `.env` path reads.
4. Добавить signed subscription URLs с TTL.
5. Добавить rate limits на subscription, payment status, webhooks.
6. Redis required in production.
7. Убрать “Нас невозможно заблокировать” из текстов.
8. Structured logging вместо silent exceptions в payment/Xray/Telegram flows.

Критерии готовности:

- forged webhook не активирует подписку;
- provider secret absence fails startup;
- subscription URL можно отозвать/перевыпустить;
- все критичные ошибки логируются.

### Фаза 2 — консистентность данных и race conditions

Цель: платежи, промокоды, referrals и Xray state не должны расходиться.

Сделать:

1. Унифицировать все оплаты через BillingService.
2. Payment state machine + stable idempotency keys.
3. Unique constraints для promocode usage и referral rewards.
4. Stable referral reward dedup.
5. Pending admin actions claim/lock/status.
6. Outbox deliveries per node.
7. Tombstones для удаления клиентов.
8. Reconciliation DB ↔ Xray.

Критерии готовности:

- repeated webhook/manual check не удваивает дни;
- concurrent promocode activation не превышает max_uses;
- user active state совпадает с Xray clients;
- failed node delivery видна и ретраится.

### Фаза 3 — node registry и multi-node traffic accounting

Цель: убрать hardcode и сделать сеть управляемой.

Сделать:

1. Ввести NodeRegistry.
2. Генерировать subscription и Xray sync config из NodeRegistry.
3. Убрать hardcoded IP/ports/SNI/short IDs из handlers.
4. Per-node health status.
5. Per-node traffic collectors с distributed locks.
6. Admin/cabinet читают агрегаты, а не live gRPC.

Критерии готовности:

- новая нода добавляется через config, а не правкой нескольких handlers;
- down-нода не попадает в recommended profiles;
- traffic totals сходятся по всем нодам;
- нет duplicate reset collectors.

### Фаза 4 — production runtime и observability

Цель: выдерживать рост и видеть проблемы до пользователей.

Сделать:

1. Multi-worker API deployment.
2. DB pool tuning.
3. Production docker/systemd healthchecks.
4. Metrics + alerts.
5. Sentry или аналог.
6. Admin pagination.
7. Убрать SSH/systemctl/journalctl из web request.
8. Backup/restore drills.

Критерии готовности:

- API выдерживает нагрузочный тест;
- admin не деградирует на тысячах пользователей;
- outbox/payment/node incidents имеют alerts;
- restore DB проверен на практике.

### Фаза 5 — продуктовая упаковка и рост

Цель: не затеряться среди VPN-сервисов.

Сделать:

1. Переписать welcome и landing copy вокруг “простой VPN в Telegram”.
2. Сделать профили “режимами”, а не списком стран.
3. Добавить QR-code.
4. Сделать visible referral dashboard.
5. Добавить blogger promo codes + analytics.
6. Запустить инструкции под iOS/Android/Windows/macOS.
7. Сделать Family/device-key тарифы.
8. Запустить status/changelog канал.
9. Добавить human-readable privacy/transparency page.

Критерии готовности:

- пользователь понимает ценность за 10 секунд;
- trial activation → first connection измеряется;
- referral conversion измеряется;
- support load на 100 пользователей снижается.

---

## 7. Рекомендуемый порядок запуска

### До закрытой беты

- Фаза 0 полностью.
- Фаза 1 минимум по платежам, секретам и subscription security.
- Ограничить число пользователей вручную.
- Проверить оплату, продление, истечение, удаление, outbox, Xray add/remove.

### Закрытая бета: первые 100–300 пользователей

- 3 дня trial.
- Ручная поддержка.
- Собирать причины отказов.
- Измерять trial → paid.
- Проверять нагрузку на Xray и backend.
- Начать рефералку среди первых пользователей.

### Pre-launch: 300–1000 пользователей

- Фаза 2 и 3.
- NodeRegistry.
- Per-node stats.
- Admin pagination.
- Видимая рефералка.
- Промокоды для микро-блогеров.

### Public launch

- Фаза 4 минимум: multi-worker, Redis, metrics, alerts, DB pool.
- Фаза 5: landing, QR, instructions, referral dashboard, status page.
- Нагрузочный тест на subscription endpoint, payment callbacks, bot flows, admin dashboard.

---

## 8. Каналы роста

1. **Telegram micro-channels**
   - iPhone/Android;
   - ChatGPT/AI;
   - YouTube/video;
   - удалёнка/фриланс;
   - локальные городские каналы.

2. **Referral program**
   - invited user получает trial;
   - referrer получает дни после оплаты;
   - milestones за 5/20/50 оплативших друзей.

3. **Blogger promo codes**
   - отдельный code/link на автора;
   - CPA или recurring commission;
   - готовые креативы и инструкции.

4. **Problem-driven content**
   - “Как подключить VPN на iPhone за 2 минуты”;
   - “Как открыть ChatGPT с телефона”;
   - “Как смотреть YouTube без лагов”;
   - “VPN для семьи: отдельные ключи на устройства”.

5. **Trust content**
   - privacy page;
   - status channel;
   - changelog;
   - explanation of logs and data deletion.

---

## 9. Smoke/load/failure checklist

Перед релизом:

```bash
python -m compileall app
```

```bash
python -m pytest
```

```bash
alembic current
```

```bash
curl -fsS http://127.0.0.1:8000/health
```

```bash
curl -fsS -H "Authorization: Bearer $SYNC_NODES_TOKEN" https://$WEBHOOK_URL_DOMAIN/webhook/sync-nodes-777 | jq .inbounds
```

Failure scenarios:

- YooKassa timeout;
- AnyPay invalid signature;
- duplicate webhook;
- Telegram API failure;
- Xray gRPC unavailable;
- one node down;
- DB temporarily unavailable;
- Redis temporarily unavailable;
- two workers process same outbox batch;
- concurrent promocode activation;
- expired user removal fails in Xray;
- subscription link leaked and rotated.

---

## 10. Финальный вывод

Проект может стать сильным VPN-сервисом, если не пытаться конкурировать с крупными VPN количеством стран и громкими обещаниями. Лучшая стратегия — сделать **самый удобный Telegram-first VPN для обычного пользователя**:

- 3 дня бесплатно;
- подключение в один клик;
- готовые режимы под задачи;
- отдельные device/family keys;
- понятная оплата;
- живая поддержка;
- прозрачность и status updates;
- стабильная distributed architecture под капотом.

Сначала нужно закрыть security и consistency, затем сделать node registry и multi-node accounting, потом упаковать продукт и включать рост через рефералку, микро-блогеров и инструкции.
