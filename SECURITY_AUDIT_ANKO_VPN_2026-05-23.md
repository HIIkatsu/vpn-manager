# Comprehensive Security Audit — AnKo VPN Manager (v2)

**Audit date:** 2026-05-23 (UTC)  
**Role:** Principal Engineer / Tech Lead / Security Auditor (strict mode)  
**Scope reviewed:** FastAPI API, Aiogram bot, async SQLAlchemy + PgBouncer interactions, Xray gRPC integration, billing/webhooks, background workers, security helpers, shell watchdog scripts.

---

## Executive Summary

**Release readiness:** ❌ **NOT READY FOR PRODUCTION**.  
Project has a good foundation (UTC usage mostly correct, centralized transaction context exists, basic auth checks present), but there are still architectural and security flaws that can break behavior under load or weaken security guarantees.

### Top release blockers
1. **Unit-of-Work (UoW) architectural invariant is broken in worker flows** (manual session lifecycle + manual commits).  
2. **Webhook auth fallback via query token** (`?token=`) risks credential leakage and replay.  
3. **Dependency injection contract mismatch** (`UserService` constructed with wrong type in one path), latent runtime failure risk.  
4. **Rate limiter fail-open and non-atomic design** can be bypassed under failure/concurrency.

---

## Severity Matrix

| ID | Severity | Area | Short title |
|---|---|---|---|
| C-01 | CRITICAL | DB/UoW | Worker transactions bypass `session_scope` |
| C-02 | CRITICAL | Security/Auth | Webhook token accepted from query string |
| C-03 | CRITICAL | Architecture/DI | `UserService` contract violated in router |
| H-01 | HIGH | Security/Rate limiting | Rate limiter race + fail-open on errors |
| H-02 | HIGH | Async/Perf | Blocking file I/O in async Xray config init |
| H-03 | HIGH | Resource lifecycle | gRPC channel has no explicit shutdown hook |
| H-04 | HIGH | Observability | Critical flows missing structured log context |
| M-01 | MEDIUM | Input validation | Admin forms have weak constraints |
| M-02 | MEDIUM | Error handling | Silent exception swallowing in critical helpers |
| M-03 | MEDIUM | Ops hygiene | Minor reliability signals / static-check gaps |

---

## Detailed Findings

## C-01 — Worker transactions bypass `session_scope`
**Location:** `app/runtime/workers.py` (functions `run_outbox_delivery_iteration`, `run_xray_reconciliation_iteration`, `run_auto_expiry_iteration`).  
**Evidence:** direct `async_session_maker()` usage and manual commits.

### Why it will break in production
- Your architectural invariant says **all transactions must go through `session_scope`**. API/Bot follows it; workers do not.
- In high load with PgBouncer, inconsistent transaction handling between subsystems causes non-uniform rollback/commit behavior and harder incident recovery.
- Long-running worker loops that combine DB + external network calls inside one session risk prolonged transaction occupancy.

### Best solution (recommended)
**Refactor workers to always use `session_scope` + split DB transaction scope from external I/O where possible.**  
For outbox/reconciliation, keep transaction windows short:
1) read batch + mark “in-progress” in tx A, commit;  
2) perform external calls outside transaction;  
3) persist results in tx B.

### Minimal fix snippet
```python
from app.services.transaction import session_scope

async def run_outbox_delivery_iteration(batch_size: int = 100) -> tuple[int, int]:
    async with session_scope(async_session_maker) as session:
        repo = OutboxRepository(session)
        # business logic...
        return delivered, failed
```

---

## C-02 — Webhook token accepted from query string
**Location:** `app/api/routers/billing_router.py` (`shared_token = ... or request.query_params.get("token")`), fallback path in `app/services/yookassa_service.py`.

### Why it will break / be exploited
- Query parameters leak to reverse-proxy logs, observability traces, link previews, browser history, and referrers.
- Secret leakage enables replay and bypass attempts.
- This undermines otherwise good auth checks.

### Best solution (recommended)
1. **Completely remove query-token auth.**
2. Keep only: Basic auth from YooKassa + optional HMAC header + private shared token header.
3. Add replay defense: store webhook event idempotency keys with TTL.
4. Keep strict IP allowlist validation behind trusted proxy policy.

### Minimal fix snippet
```python
# billing_router.py
shared_token = request.headers.get("x-yookassa-webhook-token")
# no query-param fallback
```

---

## C-03 — `UserService` dependency contract mismatch
**Location:** `app/api/routers/subscription_router.py` constructs `UserService(session)` while `UserService` expects `UserRepository`.

### Why it will break in production
- Current path may work accidentally for one method, but violates service contract.
- Later refactors or method reuse can throw runtime `AttributeError` under live traffic.
- This is an architectural time-bomb (implicit duck-typing mismatch).

### Best solution (recommended)
- Enforce strict constructor typing and a single DI factory for service creation.
- Never instantiate service directly in routers.

### Minimal fix snippet
```python
from app.db.repositories.user_repo import UserRepository
user_service = UserService(UserRepository(session))
```

---

## H-01 — Rate limiter race condition + fail-open behavior
**Location:** `app/core/security.py` (`SharedRateLimiter.allow`).

### Why it will break in production
- `SELECT COUNT` then `INSERT` is non-atomic => bursts can exceed limits.
- On SQLite errors method returns `True` (fail-open), disabling protection exactly when system is unstable.

### Best solution (recommended)
**Replace with Redis atomic limiter** (`INCR` + `EXPIRE`, or sliding-window Lua script).  
Fallback policy for webhook security should be **fail-closed** (deny on limiter backend errors after short grace threshold).

### Better architecture
- Per-key budget in Redis.
- Distinct limits per endpoint class.
- Central middleware for consistent enforcement.

---

## H-02 — Blocking disk I/O inside async flow
**Location:** `app/services/xray_manager.py` (`open` + `json.load` in `_init_config`).

### Why it will break in production
- Cold start or first request can stall event loop.
- Under concurrent startup requests p95/p99 latency spikes.

### Best solution (recommended)
- Preload Xray config in app startup (`lifespan`) once.
- Or wrap sync read in `asyncio.to_thread` and guard with `asyncio.Lock`.

---

## H-03 — gRPC channel lifecycle missing explicit shutdown
**Location:** `app/services/xray_manager.py` singleton channel creation exists; no explicit close in shutdown hooks.

### Why it will break in production
- During rolling restart / worker reload, dangling channels and file descriptors can accumulate transiently.
- Recovery becomes noisy under frequent deploys.

### Best solution (recommended)
- Implement `XrayManager.close_channel()` and call it from API/bot/worker shutdown.
- Add health checks and reconnect strategy with exponential backoff/jitter.

### Fix snippet
```python
@classmethod
async def close_channel(cls):
    if cls._channel is not None:
        await cls._channel.close()
        cls._channel = None
```

---

## H-04 — Structured logging context gaps in critical operations
**Location examples:**
- `app/runtime/workers.py` warning/exception logs without full billing/xray context.
- `app/services/xray_manager.py` retry logs lack consistent `request_id/payment_id/event_id` mapping.
- `app/services/yookassa_service.py` remote fetch warnings not consistently correlated.

### Why it will break in production
- Incident triage becomes blind; hard to correlate one payment/webhook/user with downstream Xray operations.
- MTTR increases significantly.

### Best solution (recommended)
- Mandatory logging contract for critical flows:
  - `request_id`, `payment_id`, `event_id`, `telegram_id`, `action_source`, `attempt`, `endpoint`.
- Enforce helper wrapper around logger to avoid ad-hoc `extra` maps.

---

## M-01 — Weak input constraints in admin form handlers
**Location:** `app/api/routers/admin_router.py` (`telegram_id: str`, unbounded `days`).

### Why it matters
- Bad/malicious admin input can create giant/unrealistic subscription values.
- Data quality and operational mistakes risk.

### Best solution (recommended)
Use constrained types (`Annotated`) at boundary.
```python
telegram_id: Annotated[int, Form(gt=0, lt=10**11)]
days: Annotated[int, Form(gt=0, le=3650)]
```

---

## M-02 — Silent exception swallowing in sensitive paths
**Location:** multiple broad `except Exception: pass/continue/return default` patterns.

### Why it matters
- Hides fault domains.
- Can convert hard failures into silent insecure states.

### Best solution (recommended)
- Replace silent handlers with structured logs + explicit policy (`fail-open` vs `fail-closed`) per component.
- Security checks should default to **fail-closed**.

---

## M-03 — Reliability hygiene / static check gaps
**Location:** e.g. duplicate imports (`billing_router.py`) and minor code hygiene issues.

### Why it matters
- Not directly exploitable, but shows weak CI guardrails.

### Best solution (recommended)
- Add/strictly enforce `ruff`, `mypy` (or pyright), and focused security linting (`bandit`, datetime UTC rule).

---

## UTC / Timezone Compliance Review

### Result
✅ Runtime code paths reviewed use timezone-aware UTC (`datetime.now(timezone.utc)`).  
❗ Recommendation: enforce via lint rule (`ruff` DTZ equivalent / flake8-datetimez) so naive datetimes cannot be introduced later.

---

## Multi-step Remediation Plan (Production-hardening Roadmap)

## Phase 1 — Immediate blockers (1–2 days)
1. Remove query token fallback in webhook auth.  
2. Fix `UserService` wiring in `subscription_router` and prohibit direct service instantiation in routers.  
3. Convert worker DB usage to `session_scope` and remove manual commit paths.
4. Add regression tests for above three areas.

**Exit criteria:** no direct `async_session_maker()` in business workers; no `request.query_params.get("token")`; DI checks passing.

## Phase 2 — Security correctness (2–4 days)
1. Replace SQLite rate limiter with Redis atomic limiter.
2. Define fail-closed behavior for webhook security checks.
3. Add webhook replay/idempotency cache with TTL.

**Exit criteria:** load test proves limiter correctness under concurrency burst; security checks deterministic under backend failures.

## Phase 3 — Reliability and lifecycle hardening (2–3 days)
1. Move Xray config loading to startup preload or thread-offloaded lazy init.
2. Add explicit gRPC channel close on shutdown hooks (API + bot + worker).
3. Add resilient reconnect/backoff policy and telemetry counters.

**Exit criteria:** repeated rolling restarts do not increase open FD count; p99 startup latency stable.

## Phase 4 — Observability and operability (2–3 days)
1. Standardize logging schema for billing/xray/admin critical paths.
2. Add dashboards: webhook auth failures, outbox retries, gRPC errors, stale processing reclaim count.
3. Add alerts on anomalous retry/failure rates.

**Exit criteria:** every critical operation traceable end-to-end by correlation ids.

## Phase 5 — Pre-prod validation gate (2–5 days)
1. Run integration tests against PgBouncer transaction pooling mode.
2. Chaos/load scenarios:
   - webhook burst and replay,
   - Xray temporary unavailability,
   - DB latency spikes,
   - bot API intermittent failures.
3. Security regression checklist sign-off.

**Exit criteria:** agreed SLO/SLA and security acceptance criteria met.

---

## Final Go-Live Assessment

**Current status:** ❌ **Hold release.**  
**Target status after phases 1–3:** ⚠️ Candidate for controlled staging rollout.  
**Target status after phases 1–5:** ✅ Production-ready with monitored canary release.

