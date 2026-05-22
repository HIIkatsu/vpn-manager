# Runtime Decomposition Runbook (API + Bot + Worker)

Этот runbook переводит текущий монолитный systemd-процесс на 3 отдельных процесса **без миграции базы и без потери данных**.

## Важные принципы

- База данных **не пересоздается** и не трогается: используется существующий PostgreSQL на `localhost:5433` из `.env`.
- Новые процессы читают те же переменные окружения (`EnvironmentFile=.env`) и потому подключаются к текущей БД.
- Миграции БД в этом шаге не требуются.

## Что уже подготовлено в репозитории

- Entry points:
  - `app/runtime/bot_entrypoint.py`
  - `app/runtime/worker_entrypoint.py`
- Шаблоны systemd unit-файлов:
  - `deploy/systemd/vpn-api.service`
  - `deploy/systemd/vpn-bot.service`
  - `deploy/systemd/vpn-worker.service`

## Предварительная проверка на сервере

1. Перейти в директорию проекта:
   ```bash
   cd /opt/vpn-manager
   ```
2. Проверить, что `.env` содержит боевой `DATABASE_URL` с `localhost:5433`.
3. Проверить импорты/синтаксис:
   ```bash
   ./.venv/bin/python -m compileall app
   ```

## План перекатки (минимальный downtime)

> Ниже предполагается, что текущий старый сервис называется `vpn-monolith.service`.

1. Скопировать и активировать **только API** unit:
   ```bash
   sudo cp deploy/systemd/vpn-api.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now vpn-api.service
   sudo systemctl status vpn-api.service --no-pager
   ```
2. Проверить health/API endpoint'ы через nginx/локально.
3. Запустить **bot** и проверить реакцию на `/start`:
   ```bash
   sudo cp deploy/systemd/vpn-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now vpn-bot.service
   sudo systemctl status vpn-bot.service --no-pager
   ```
4. Запустить **worker**:
   ```bash
   sudo cp deploy/systemd/vpn-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now vpn-worker.service
   sudo systemctl status vpn-worker.service --no-pager
   ```
5. Только после успешной проверки API+bot+worker остановить старый монолит:
   ```bash
   sudo systemctl disable --now vpn-monolith.service
   ```

## Проверка после перекатки

- Логи API:
  ```bash
  sudo journalctl -u vpn-api.service -f
  ```
- Логи бота:
  ```bash
  sudo journalctl -u vpn-bot.service -f
  ```
- Логи воркера:
  ```bash
  sudo journalctl -u vpn-worker.service -f
  ```
- Проверить, что бот отвечает на `/start` и callback-кнопки.
- Проверить, что webhook YooKassa получает 200/401/403/429 в ожидаемых сценариях.

## Rollback (быстрый откат)

Если что-то пошло не так:

```bash
sudo systemctl disable --now vpn-worker.service vpn-bot.service vpn-api.service
sudo systemctl enable --now vpn-monolith.service
```

После отката проверить доступность API и ответы бота.
