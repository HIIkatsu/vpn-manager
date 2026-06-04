# Active Push синхронизация клиентов Xray

Цель этой схемы — убрать задержку `cron + pull`: мастер в Финляндии сразу отправляет `add/remove` клиента на все второстепенные ноды. Получатель на ноде меняет только пользователей VLESS через Xray gRPC `AlterInbound`; файл `/usr/local/etc/xray/config.json`, маршрутизация, inbounds/outbounds, балансировка, SOCKS-проксирование и локальные правила не перезаписываются.

## Что настраивается на мастере

1. Сгенерируйте общий секрет:

```bash
openssl rand -hex 32
```

2. В `/opt/vpn-manager/.env` на мастере добавьте один и тот же секрет и URL трёх получателей:

```env
SYNC_NODES_TOKEN=<секрет из openssl>
SYNC_PUSH_NODES=https://germany.example.com/internal/xray/client,https://netherlands.example.com/internal/xray/client,https://russia.example.com/internal/xray/client
SYNC_PUSH_TIMEOUT_SECONDS=5
```

`SYNC_PUSH_NODES` также принимает JSON, если нужны понятные имена в логах:

```env
SYNC_PUSH_NODES=[{"name":"germany","url":"https://germany.example.com/internal/xray/client"},{"name":"netherlands","url":"https://netherlands.example.com/internal/xray/client"},{"name":"russia","url":"https://russia.example.com/internal/xray/client"}]
```

3. Перезапустите воркер мастера:

```bash
sudo systemctl restart vpn-worker.service
```

Если админские pending actions используются через API, перезапустите API тоже:

```bash
sudo systemctl restart vpn-api.service
```

## Что поднять на каждой из 3 второстепенных нод

Команды ниже выполняются на каждой ноде. Пример рассчитан на размещение проекта в `/opt/vpn-manager`.

```bash
cd /opt/vpn-manager
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Создайте `/opt/vpn-manager/.env.node`:

```env
SYNC_NODES_TOKEN=<тот же секрет, что на мастере>
XRAY_CONFIG_PATH=/usr/local/etc/xray/config.json
# Обычно пусто: receiver сам найдёт dokodemo-door API inbound в config.json.
XRAY_GRPC_TARGET=
XRAY_REQUEST_TIMEOUT_SECONDS=5
XRAY_REQUEST_RETRIES=2
SYNC_MAX_SKEW_SECONDS=300
LOG_LEVEL=INFO
```

Установите systemd unit:

```bash
sudo cp /opt/vpn-manager/deploy/systemd/xray-node-receiver.service /etc/systemd/system/xray-node-receiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now xray-node-receiver.service
sudo systemctl status xray-node-receiver.service
```

Receiver слушает только `127.0.0.1:8090`. Опубликуйте его через ваш nginx на ноде по HTTPS, не меняя Xray config. Минимальный location:

```nginx
location = /internal/xray/client {
    proxy_pass http://127.0.0.1:8090/internal/xray/client;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location = /health {
    proxy_pass http://127.0.0.1:8090/health;
}
```

Затем:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -fsS https://<node-domain>/health
```

## Проверка доставки

На мастере после оплаты или ручного pending action воркер берёт событие из `outbox_events`, добавляет/удаляет клиента локально в Финляндии и параллельно отправляет signed HTTPS POST на все URL из `SYNC_PUSH_NODES`. Событие помечается `processed` только если локальная нода и все второстепенные ноды ответили успешно; иначе стандартный outbox retry повторит доставку. Операции идемпотентны: `already exists` для add и `not found` для remove считаются успешными.

На второстепенной ноде смотрите логи:

```bash
journalctl -u xray-node-receiver.service -n 100 --no-pager
```
