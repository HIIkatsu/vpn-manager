# Release hardening checklist

Run this checklist before production releases and after infrastructure changes. Commands that target production nodes must be executed from the appropriate host with production environment variables loaded.

## Smoke checks

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
curl -fsS -H "Authorization: Bearer $SYNC_NODES_TOKEN" https://$WEBHOOK_URL_DOMAIN/webhook/sync-nodes-777 | jq .inbounds
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

## Load checks

- Run concurrent subscription fetches for active and inactive users and confirm no DB write locks or elevated error rate.
- Run two worker processes in staging and verify outbox events and subscription notifications are delivered once.
- Check `/admin/audit` while generating user traffic and confirm API health probes still respond within the SLO.

## Failure-injection scenarios

- YooKassa API timeout: payment compensation logs one failed `payment_id` and continues the rest of the batch.
- Telegram API timeout/rate limit: notification marker is retried after `retry_at` and no duplicate successful notification is sent.
- Xray gRPC unavailable: outbox events move back to retryable state and are not lost.
- A worker node stops pulling config for 5 minutes: health/audit surfaces the node issue without blocking the API event loop.
- HAProxy sees only 1 of 3 backend nodes: subscriptions still contain all configured endpoints and monitoring alerts fire.
- Database temporarily unavailable: service logs structured exceptions and recovers after connectivity is restored.
- Two worker processes are started simultaneously: payment compensation, outbox, and notification dedup remain idempotent.
