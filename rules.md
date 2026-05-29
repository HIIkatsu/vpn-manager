# AnKo VPN Manager — аудит рисков и план исправлений

Документ фиксирует найденные проблемы в backend-проекте VPN-менеджера с учетом реальной инфраструктуры из 4 серверов:

- **Финляндия / Main (`150.251.152.174`)** — FastAPI backend, БД, Telegram-бот, локальный Xray, Nginx reverse proxy на `127.0.0.1:8001`.
- **Германия / Worker (`132.243.194.119`)** — только Xray, раз в минуту забирает конфиг с Main через `/webhook/sync-nodes-777`.
- **Нидерланды / Worker (`194.50.94.177`)** — аналогично Германии.
- **РФ / Balancer (`132.243.230.173`)** — HAProxy принимает трафик на `443` и балансирует его на `20443` евро-нод.

Главный принцип исправлений: **не переписывать проект с нуля и не ломать текущую рабочую схему**. Локальные gRPC-вызовы в Xray и генерация JSON-конфига для удаленных Xray-нод должны сохраниться, но получить защиту, идемпотентность и нормальные границы транзакций.

---

## Итоговая оценка проекта

**Текущая оценка: 5/10.**

Проект имеет рабочую бизнес-логику, понятную доменную модель, базовые timeout/retry для части внешних вызовов, outbox-подход для Xray-активаций и попытки защитить платежный webhook. Но в production-схеме с 4 серверами есть критические риски:

- публичная выдача полного Xray-конфига, включая Reality private key;
- неиспользуемая webhook-аутентификация YooKassa;
- возможная потеря/двойной учет трафика из-за `reset=True` в нескольких местах;
- гонки в outbox при двух worker-процессах;
- долгие Xray/Telegram/SSH операции внутри открытых DB-сессий;
- блокировка event loop в admin-аудите;
- hardcoded IP/ключи;
- проблема кодировки `xray_manager.py`, из-за которой чистая компиляция проекта падает.

После исправлений **Этапа 1** проект можно поднять примерно до **7/10**. После **Этапа 2** — до **8/10**. После **Этапа 3** — до **8.5-9/10**, если дополнительно подтвердить устойчивость нагрузочными и отказоустойчивыми тестами.

---

# Этап 1 — критическая безопасность и запускоспособность

Цель этапа: закрыть уязвимости, которые могут привести к компрометации всей VPN-инфраструктуры или срыву деплоя. Эти исправления должны быть сделаны первыми.

## 1.1. Закрыть `/webhook/sync-nodes-777`

### Найденная проблема

Эндпоинт `/webhook/sync-nodes-777` сейчас отдает полный Xray JSON-конфиг без аутентификации и без IP allowlist. Ответ содержит список активных клиентов, Reality-настройки и приватный ключ.

Это критично, потому что Германия и Нидерланды действительно должны забирать этот конфиг по cron, но сейчас то же самое может сделать любой внешний клиент, если знает URL.

### Побочный эффект для 4-серверной архитектуры

Если этот endpoint будет скомпрометирован, злоумышленник получает материал для подключения/анализа всей евро-сети:

- активные client UUID;
- inbound tags и порты;
- Reality `privateKey`;
- `shortIds`;
- SNI/serverNames;
- схему портов `443`, `10444`, `10445`, а также косвенно связку с HAProxy/iptables.

### Лучшее решение

Использовать **двухслойную защиту**:

1. **Bearer-токен** в заголовке `Authorization`.
2. **IP allowlist** для известных worker-нод.

В `settings.py` добавить:

```python
SYNC_NODES_TOKEN: str
SYNC_NODES_IP_ALLOWLIST: str = "132.243.194.119/32,194.50.94.177/32,150.251.152.174/32"
XRAY_REALITY_PRIVATE_KEY: str
```

В endpoint добавить проверку:

```python
from fastapi import HTTPException, Request, status
import hmac

@router.get("/webhook/sync-nodes-777")
async def generate_nodes_config(request: Request, session: AsyncSession = Depends(get_async_session)):
    auth = request.headers.get("authorization", "")
    expected = f"Bearer {settings.SYNC_NODES_TOKEN}"
    if not hmac.compare_digest(auth, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    client_ip = request.client.host if request.client else ""
    cidrs = [x.strip() for x in settings.SYNC_NODES_IP_ALLOWLIST.split(",") if x.strip()]
    if not ip_in_allowlist(client_ip, cidrs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
```

Cron на worker-нодах должен использовать:

```bash
curl -fsS -H "Authorization: Bearer $SYNC_NODES_TOKEN" https://neurosmmai.ru/webhook/sync-nodes-777
```

Важно: **формат JSON не менять**, чтобы Германия и Нидерланды продолжили применять конфиг без изменения логики Xray.

---

## 1.2. Убрать Reality private key из кода

### Найденная проблема

Reality private key захардкожен прямо в `subscription_router.py` при генерации remote-конфига.

### Почему это опасно

Даже приватный репозиторий не должен быть хранилищем runtime-секретов. При утечке кода или логов деплоя придется срочно ротировать ключи на всех нодах и обновлять подписки.

### Лучшее решение

Хранить private key только в `.env`/secret storage:

```python
XRAY_REALITY_PRIVATE_KEY: str
```

И использовать:

```python
prv = settings.XRAY_REALITY_PRIVATE_KEY
sid = settings.VLESS_SHORT_ID
```

При ротации ключей нужен порядок:

1. добавить новый public/private key pair на Main;
2. обновить генерацию подписок с новым public key;
3. обновить sync-конфиг для worker-нод;
4. дождаться применения cron на Германии/Нидерландах;
5. перезапустить Xray/проверить health;
6. только потом удалять старую пару.

---

## 1.3. Включить фактическую защиту YooKassa webhook

### Найденная проблема

В коде уже есть методы проверки Basic Auth/webhook secret, настройки allowlist и remote verification, но в `yookassa_webhook` реально не вызывается `is_valid_webhook_auth`, а `YOOKASSA_WEBHOOK_IP_ALLOWLIST` не применяется.

### Почему это опасно

S2S-проверка платежа через YooKassa снижает риск фейковой активации, но не защищает от:

- DoS на endpoint;
- вынужденных походов backend-а в YooKassa API;
- засорения replay guard;
- replay/duplicate race до прихода легитимного webhook.

### Лучшее решение

В начале webhook после вычисления `client_ip` добавить:

```python
if settings.YOOKASSA_WEBHOOK_IP_ALLOWLIST:
    cidrs = [x.strip() for x in settings.YOOKASSA_WEBHOOK_IP_ALLOWLIST.split(",") if x.strip()]
    if not ip_in_allowlist(client_ip, cidrs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

if settings.YOOKASSA_WEBHOOK_SECRET or settings.YOOKASSA_WEBHOOK_AUTH:
    if not yookassa.is_valid_webhook_auth(
        request.headers.get("authorization"),
        request.headers.get("x-webhook-secret"),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook auth")
```

Replay guard лучше вызывать **после**:

1. успешного парсинга notification;
2. S2S-проверки `fetch_remote_payment`;
3. нахождения локального `Payment`;
4. проверки amount/currency/metadata.

Иначе атакующий может занять replay key до легитимного webhook.

---

## 1.4. Починить кодировку `app/services/xray_manager.py`

### Найденная проблема

`python -m compileall app` падает на `app/services/xray_manager.py` с `SyntaxError: Non-UTF-8 code starting with ...`. Причина — битые комментарии в не-UTF-8 кодировке.

### Почему это опасно

Это может ломать:

- чистый Docker build;
- CI;
- запуск после обновления Python;
- импорт модуля в новой среде;
- автогенерацию bytecode.

### Лучшее решение

Перекодировать файл в UTF-8 и заменить битые комментарии нормальными русскими/английскими комментариями. Не менять gRPC-логику, только кодировку и текст комментариев.

Критерий готовности:

```bash
python -m compileall app
```

должен проходить без ошибок.

---

# Этап 2 — консистентность данных, race conditions и транзакции

Цель этапа: убрать потери трафика, повторную доставку событий, зависание соединений и рассинхрон БД/Xray.

## 2.1. Исправить сбор статистики трафика

### Найденная проблема

Xray counters сбрасываются из нескольких мест:

- worker вызывает `get_live_traffic_stats(reset=True)`;
- админка тоже вызывает `get_live_traffic_stats(reset=True)`.

Это создает гонку: кто первый прочитал статистику, тот ее сбросил. Второй процесс увидит 0.

### Дополнительная проблема ключей

Локальный Xray получает client email как `telegram_id`, outbox вызывает `xray.add_client(email=str(payload["telegram_id"]), uuid=payload["uuid"])`.

Но `traffic_stats_loop` пытается матчить статистику по `User.vless_uuid`. Для удаленных Xray-нод sync-конфиг вообще задает email как первые 8 символов UUID. Получаются три разных идентификатора:

- локальный Xray: `telegram_id`;
- worker loop ожидает: полный `vless_uuid`;
- remote JSON: `uuid[:8]`.

### Лучшее решение

Минимальное безопасное решение без ломки текущих пользователей:

1. Только worker имеет право вызывать `reset=True`.
2. Админка должна использовать `reset=False` или показывать накопленные значения из БД.
3. Локальную статистику матчить по тому email, который реально используется в локальном Xray — сейчас это `telegram_id`.
4. На будущее ввести единое поле `xray_email` и использовать его одинаково:
   - в gRPC add/remove;
   - в remote JSON;
   - в парсинге stats;
   - в БД.

Рекомендуемый целевой вариант:

```python
xray_email = str(user.telegram_id)
```

Для удаленных нод тоже лучше использовать этот email, а не `uuid[:8]`, если нет строгой причины скрывать telegram id. Если скрывать нужно, надо хранить отдельный стабильный `xray_email` в БД.

---

## 2.2. Сделать outbox действительно конкурентно-безопасным

### Найденная проблема

Outbox выбирает pending-события через `FOR UPDATE SKIP LOCKED`, но транзакция закрывается до внешнего Xray-вызова. После закрытия транзакции lock исчезает, а событие все еще `pending`.

При двух worker-процессах одно и то же событие может быть доставлено дважды.

### Почему это опасно

`xray.add_client` сейчас частично идемпотентен, потому что `already exists` считается успехом. Но это не гарантирует безопасность для будущих типов событий и не защищает от лишних gRPC-вызовов, гонок статусов и шумных логов.

### Лучшее решение

Ввести статусную модель outbox:

- `pending` — готово к обработке;
- `processing` — захвачено конкретным worker-ом;
- `processed` — успешно доставлено;
- `failed` — исчерпаны попытки;
- `retry_at` — время следующей попытки.

Паттерн обработки:

1. Короткая DB-транзакция:
   - выбрать `pending` где `retry_at <= now`;
   - поставить `processing`;
   - увеличить `attempts`;
   - сохранить `locked_at`.
2. Внешний Xray-вызов вне транзакции.
3. Короткая DB-транзакция:
   - success -> `processed`;
   - transient fail -> `pending` + backoff;
   - attempts exceeded -> `failed`.

Такой подход сохраняет текущую outbox-архитектуру и не требует переписывать биллинг.

---

## 2.3. Не держать DB-сессии во время Xray/Telegram/SSH

### Найденная проблема

В `expiry_loop` внутри `session_scope` выполняются Xray remove и Telegram send. В `admin_apply` внутри request-сессии выполняются gRPC add/remove. В `admin/audit` внутри async endpoint выполняются блокирующие SSH/subprocess.

### Почему это опасно

Для SQLite это может давать долгие write locks. Для Postgres — удержание connection из pool и длинные транзакции. В любом варианте внешний timeout может держать DB-сессию открытой без необходимости.

### Лучшее решение

Разделить операции на фазы:

1. DB snapshot:
   - выбрать пользователей/действия;
   - сохранить только id, telegram_id, uuid, action_type.
2. Внешние вызовы:
   - Xray gRPC;
   - Telegram API;
   - SSH/subprocess.
3. DB finalize:
   - применить изменения;
   - удалить pending action;
   - записать ошибку/статус.

Пример для `expiry_loop`:

```python
# 1. short DB read
expired_snapshots = [...]

# 2. external calls outside DB transaction
for user in expired_snapshots:
    removed = await xray.remove_client(email=str(user.telegram_id))
    notify_result = await send_expiry_message(...)

# 3. short DB write
async with session_scope(async_session_maker) as session:
    user = await session.get(User, user_id)
    user.is_active = False
```

Telegram-уведомление не должно блокировать commit статуса подписки.

---

## 2.4. Разделить read-only и write DB dependencies

### Найденная проблема

Текущий `get_async_session` использует `session_scope`, который делает commit после любого успешного endpoint. Это удобно для write endpoint-ов, но плохо для read-only endpoint-ов.

GET `/admin` фактически пишет трафик в БД, а GET `/cabinet/{uuid}` меняет `preferred_os` и вручную вызывает `session.commit()`, после чего dependency сделает второй commit.

### Лучшее решение

Ввести две зависимости:

```python
async def get_read_session():
    async with async_session_maker() as session:
        yield session

async def get_write_session():
    async with session_scope(async_session_maker) as session:
        yield session
```

Правило:

- endpoint только читает — `get_read_session`;
- endpoint меняет данные — `get_write_session`;
- ручные `session.commit()` внутри handler-ов запрещены, кроме миграционных/служебных scripts.

---

## 2.5. Исправить удаление пользователей после истечения подписки

### Найденная проблема

`admin_apply` перед удалением пользователя вручную удаляет связанные `Payment` и `PendingAction`, а `expiry_loop` делает просто `session.delete(user)`.

При SQLite без включенного `PRAGMA foreign_keys=ON` каскад может не сработать. При Postgres сработает, но поведение будет отличаться между окружениями.

### Лучшее решение

Сделать единый путь удаления пользователя:

```python
async def delete_user_with_relations(session: AsyncSession, user: User) -> None:
    await session.execute(delete(Payment).where(Payment.user_id == user.id))
    await session.execute(delete(PendingAction).where(PendingAction.user_id == user.id))
    await session.delete(user)
```

И использовать его и в `admin_apply`, и в `expiry_loop`.

---

# Этап 3 — hardening, наблюдаемость и инфраструктурная зрелость

Цель этапа: довести проект до устойчивого production-уровня, где инциденты диагностируются, секреты ротируются, а админские операции не блокируют API.

## 3.1. Harden `/admin/audit`

### Найденная проблема

`/admin/audit` вызывает `subprocess.check_output` внутри async handler и вставляет логи в HTML без escaping. SSH-команды для удаленных нод запускаются через `shell=True`.

### Почему это опасно

- блокируется event loop FastAPI;
- один медленный SSH может задерживать другие запросы;
- логи Xray могут стать XSS-вектором в браузере администратора;
- `shell=True` не нужен и увеличивает риск при будущих изменениях.

### Лучшее решение

1. Перенести subprocess в `asyncio.to_thread`.
2. Убрать `shell=True`.
3. Использовать список аргументов:

```python
[
    "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=3",
    "-o", "PasswordAuthentication=no",
    f"root@{ip}",
    "journalctl -u xray -p 3 -n 15 --no-pager",
]
```

4. Перед вставкой логов в HTML использовать:

```python
from html import escape
safe_logs = escape(data["logs"])
```

---

## 3.2. Вынести инфраструктурные IP, порты и ключи в settings

### Найденная проблема

IP Финляндии, Германии, Нидерландов, РФ-балансера, Reality public/private key и часть портов захардкожены в коде.

### Почему это опасно

В 4-серверной схеме смена IP/порта/ключа — это обычная инфраструктурная операция. Если значения разбросаны по коду, легко обновить подписки, но забыть audit или sync-конфиг.

### Лучшее решение

Добавить в settings:

```python
FINLAND_PUBLIC_IP: str
GERMANY_PUBLIC_IP: str
NETHERLANDS_PUBLIC_IP: str
RUSSIA_BALANCER_IP: str
XRAY_REALITY_PRIVATE_KEY: str
XRAY_REALITY_PUBLIC_KEY: str
XRAY_MAIN_PORT: int = 443
XRAY_REDIRECT_PORT: int = 20443
XRAY_RU_CLEAN_PORT: int = 10444
XRAY_RU_WHITELIST_PORT: int = 10445
```

Правило: подписки, sync JSON и admin audit должны брать значения из одного источника.

---

## 3.3. Сделать уведомления идемпотентными

### Найденная проблема

`notification_loop` хранит `notified_state` in-memory. После рестарта worker-а состояние теряется, при нескольких worker-процессах каждый процесс будет иметь свой cache. При превышении 5000 ключей cache полностью очищается.

### Лучшее решение

Создать таблицу `subscription_notifications`:

- `id`;
- `user_id`;
- `notify_type`;
- `sub_end_date`;
- `sent_at`;
- unique constraint на `(user_id, notify_type, sub_end_date)`.

Алгоритм:

1. В короткой транзакции попытаться вставить notification marker.
2. Если unique conflict — уведомление уже было, пропустить.
3. Если вставка успешна — отправить Telegram.
4. При ошибке отправки записать `last_error` и разрешить retry через `retry_at`.

---

## 3.4. Улучшить billing compensation

### Найденная проблема

`process_pending` обрабатывает batch платежей, но ошибка одного remote payment может сорвать обработку всего batch. `FOR UPDATE` хорошо работает в Postgres, но не дает полноценной row-level защиты в SQLite.

### Лучшее решение

- оборачивать каждый payment в отдельный `try/except`;
- логировать `payment_id`, `user_id`, `attempt`;
- использовать status transition `pending -> processing -> success/failed`;
- для SQLite не рассчитывать на `FOR UPDATE` как на полноценную блокировку;
- при production-нагрузке предпочесть Postgres.

---

## 3.5. Нормализовать логирование и запретить немые исключения

### Найденная проблема

В проекте есть `except Exception: pass`, особенно вокруг Telegram-уведомлений. Это скрывает реальные инциденты: блокировки ботом, rate limit Telegram, сетевые ошибки, невалидные chat_id.

### Лучшее решение

Правило:

- нельзя использовать `except Exception: pass` в production-коде;
- минимум `logger.warning(..., extra=log_context(...))`;
- для критических операций `logger.exception`;
- обязательные поля: `telegram_id`, `payment_id`, `event_id`, `action_source`, `endpoint`, если они применимы.

---

## 3.6. Добавить smoke/load/failure тесты для релиза

### Минимальный checklist

Перед каждым релизом проверять:

```bash
python -m compileall app
```

```bash
alembic current
```

```bash
curl -fsS http://127.0.0.1:8001/health
```

```bash
curl -fsS -H "Authorization: Bearer $SYNC_NODES_TOKEN" https://neurosmmai.ru/webhook/sync-nodes-777 | jq .inbounds
```

```bash
systemctl is-active xray
```

```bash
journalctl -u vpn-api -n 100 --no-pager
```

```bash
journalctl -u vpn-worker -n 100 --no-pager
```

### Failure-injection сценарии

- YooKassa API timeout;
- Telegram API timeout;
- Xray gRPC недоступен;
- одна worker-нода не забирает config 5 минут;
- HAProxy видит только 1 из 3 backend-нод;
- БД временно недоступна;
- два worker-процесса запущены одновременно.

---

# Финальный порядок выполнения

## Этап 1 — Security hotfix и запускоспособность

1. Закрыть `/webhook/sync-nodes-777` Bearer-токеном и IP allowlist.
2. Убрать Reality private key из кода в env/settings.
3. Реально включить YooKassa webhook auth и IP allowlist.
4. Перенести replay guard после базовой валидации платежа.
5. Починить UTF-8 кодировку `xray_manager.py`.
6. Прогнать `python -m compileall app`.

Ожидаемая оценка после этапа: **7/10**.

## Этап 2 — Data consistency и race conditions

1. Убрать `reset=True` из админки.
2. Унифицировать Xray email/key для статистики.
3. Ввести `processing/retry_at/failed` для outbox.
4. Вынести Xray/Telegram вызовы за пределы DB-транзакций.
5. Разделить read/write session dependencies.
6. Унифицировать удаление пользователя со связанными сущностями.

Ожидаемая оценка после этапа: **8/10**.

## Этап 3 — Production hardening

1. Harden `/admin/audit`: no `shell=True`, no event loop blocking, HTML escaping.
2. Вынести все IP/порты/ключи в settings.
3. Сделать notification dedup через БД/Redis.
4. Улучшить billing compensation per-payment isolation.
5. Запретить немые exceptions.
6. Добавить smoke/load/failure-injection checklist.

Ожидаемая оценка после этапа: **8.5-9/10**.

---

# Главный вывод

Проект не выглядит безнадежным и не требует переписывания с нуля. Основная проблема не в FastAPI/Aiogram/SQLAlchemy как таковых, а в том, что backend уже управляет распределенной VPN-инфраструктурой, но часть endpoint-ов и фоновых задач все еще написаны как для одного локального сервера.

Самое важное — сначала защитить sync endpoint и платежный webhook, затем стабилизировать статистику/outbox/транзакции, и только после этого заниматься косметикой и расширенным hardening.
