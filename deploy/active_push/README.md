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
# Обычно пусто: receiver сам найдёт локальный Xray API inbound в config.json.
XRAY_GRPC_TARGET=
XRAY_REQUEST_TIMEOUT_SECONDS=5
XRAY_REQUEST_RETRIES=2
# auto: сначала gRPC AlterInbound, при сбое — безопасная правка config.json + systemctl reload xray.
# grpc: только gRPC. config: только правка config.json + reload.
XRAY_MUTATION_MODE=auto
XRAY_BINARY=/usr/local/bin/xray
XRAY_RELOAD_COMMAND=systemctl reload xray
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


## Важный фикс Xray API inbound

Если `xray api ... --server=127.0.0.1:10085` сразу падает с `failed to dial` / `tcp handshaker shutdown`, сначала проверьте сам API inbound. В актуальной документации Xray для API используется `protocol: "tunnel"` и `settings.rewriteAddress`, а не старый `dokodemo-door` + `settings.address`:

```json
{
  "api": {
    "tag": "api",
    "services": ["HandlerService", "StatsService"]
  },
  "inbounds": [
    {
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "tunnel",
      "settings": { "rewriteAddress": "127.0.0.1" },
      "tag": "api"
    }
  ],
  "routing": {
    "rules": [
      { "type": "field", "inboundTag": ["api"], "outboundTag": "api" }
    ]
  }
}
```

После правки выполните `xray run -test -config /usr/local/etc/xray/config.json` и `systemctl reload xray`. Если gRPC API всё равно нестабилен, оставьте `XRAY_MUTATION_MODE=auto` или выставьте `XRAY_MUTATION_MODE=config`: receiver будет атомарно обновлять `settings.clients` во всех VLESS inbound, валидировать временный конфиг через `xray run -test`, заменять файл и выполнять `systemctl reload xray` без принудительного `restart`. Для этого процессу receiver нужны права на запись `XRAY_CONFIG_PATH` и запуск reload-команды; unit из этого репозитория запускает receiver от `root`, а сам HTTP-сервис слушает только `127.0.0.1` и требует HMAC-подписанный запрос. Если не хотите root-процесс, выдайте узкий sudo/ACL только на этот файл и `systemctl reload xray`, а в unit верните менее привилегированного пользователя.

## Проверка доставки

На мастере после оплаты или ручного pending action воркер берёт событие из `outbox_events`, добавляет/удаляет клиента локально в Финляндии и параллельно отправляет signed HTTPS POST на все URL из `SYNC_PUSH_NODES`. Событие помечается `processed` только если локальная нода и все второстепенные ноды ответили успешно; иначе стандартный outbox retry повторит доставку. Операции идемпотентны: `already exists` для add и `not found` для remove считаются успешными.

На второстепенной ноде смотрите логи:

```bash
journalctl -u xray-node-receiver.service -n 100 --no-pager
```
