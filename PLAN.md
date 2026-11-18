# PLAN.md — Pulse, a status and incident hub

> Consolidated plan for the built product. The original Gate 1 plan is preserved in
> git history (`048a8a0`); post-gate changes live in `DECISIONS.md`.

## Project overview

Pulse continuously checks the health of registered services (the starter kit's
`quotes`, `payments`, `claims`, `notifications`), classifies each as
`healthy / degraded / down`, opens and tracks an incident until recovery, notifies a
webhook receiver, and proves reliability against per-service targets.

Users: engineers on call (operator dashboard), the support team (webhook alerts),
customers (sanitized public status page). Internal error text (e.g. the kit's
`upstream …-db timeout at 10.12.4.7:5432`) never reaches public surfaces.

## Architecture

```
              ┌────────────────────────── Pulse (one process) ─────────────────────┐
 mock services │ Scheduler: 1 asyncio task per service (isolated, per-service      │
 :7001–7004 ──► interval/timeout)                                                 │
              │        └► Checker (httpx GET /health → ok|timeout|conn_refused|5xx) │
              │            └► State machine (pure fn: 3 fails→down, 2 ok→recover)   │
              │                ├► Incident engine (open/ack/note/resolve + timeline)│
              │                ├► Notifier (webhook POST, 3 attempts, logged)       │
              │                ├► Reliability (1h/24h/7d uptime vs target, budget)  │
              │                └► Metrics (MTTD, MTTR, checker p50/95/99, delivery) │
              │ FastAPI: JSON API (/api/*) + Jinja2 UI (/, /status, /metrics, …)    │
              └──────────────────────────────────┬─────────────────────────────────┘
                                                 ▼
                                    SQLite (WAL): services, checks,
                                    incidents, incident_events, notifications
```

## Technology stack

- **Python 3.12** — FastAPI + Uvicorn (web), httpx (async checks/notifications)
- **Jinja2** server-rendered templates (no JS build step)
- **SQLite** (WAL mode, single guarded connection, reentrant lock)
- **pytest + pytest-asyncio** — unit, integration (stub servers), acceptance
- **Docker / Compose** — starter kit services; optional Pulse container

## Main components & features

| Component | File(s) | What it does |
|---|---|---|
| Service catalogue | `store.py`, `api/routes.py`, dashboard form | CRUD via API + UI; seeded with the kit's 4 services (`seed.py`) |
| Health checker | `checker.py` | Per-request timeout, latency, error classes; raw detail kept internal |
| State machine | `state.py` | Pure function; thresholds per service (`failures_to_down=3`, `successes_to_recover=2`, `degraded_latency_ms`) |
| Scheduler | `scheduler.py` | One asyncio task per service; restart-safe state re-derivation |
| Incidents | `incidents.py`, `store.py` | Auto-open on down, auto-resolve on recovery, ack/notes, append-only timeline |
| Notifications | `notifier.py` | Sanitized JSON payloads on open/resolve; 3 attempts + backoff; delivery log |
| Reliability | `reliability.py` | Check-weighted uptime % per window vs target; error budget used/remaining |
| Metrics | `metrics.py` | MTTD, MTTR, checker success/latency percentiles, notification delivery rate |
| Operator UI | `web/routes.py`, templates | Dashboard, service detail, incident timeline + actions |
| Public status | `/status`, `/api/status` | Read-only, sanitized, plain-English states |
| JSON API | `api/routes.py` | Services CRUD, incidents (+ack/notes/resolve), status, metrics |

## Implementation phases

1. **Scaffold & plan** — repo, `PLAN.md`, `DECISIONS.md`, schema, config ✅
2. **Core logic** — checker, pure state machine, unit tests ✅
3. **Runtime loop** — scheduler, incident engine, notifier, seeding ✅
4. **Web & API** — dashboard, status page, metrics pages, JSON API, reliability ✅
5. **Verification** — integration suite (stub kit/webhook), acceptance vs real kit ✅
6. **Docs & handover** — README, REVIEW, PROGRESS, transcript export ✅

## Testing strategy

- **Unit (46)** — state machine transitions (flaky/slow/recovery), reliability and
  error-budget math, sanitizer guarantees, webhook payload shape, API contract
  (`TestClient`), navigation regression (no 0-second reload loops, links present).
- **Integration (8)** — real asyncio + stub kit/webhook: down→incident→sanitized
  webhook, recovery→resolve→webhook, ack/note timeline, every check stored.
- **Acceptance (9, `pytest -m acceptance`)** — against the running starter kit;
  auto-skipped when the kit/Pulse aren't up. Includes the leak check: the kit's
  internal error IP must never appear on `/status`, `/api/status`, or webhook payloads.
- **Run:** `python -m pytest tests -q` (54) · `… -m acceptance` (9) ·
  `scripts/verify_nav.py` (live nav) · `scripts/acceptance.sh` (POSIX variant).

## Deployment steps

```bash
# 1. Starter kit (mock services :7001–7004, webhook receiver :9900)
cd pulse-starter-kit && docker compose up --build -d

# 2. Pulse (default port 8001; 8000 commonly clashes on dev machines)
cd pulse
pip install -r requirements.txt
python run.py
```

- Optional container: `docker compose up --build` in `pulse/` (targets
  `host.docker.internal`).
- Config via env: `PULSE_PORT`, `PULSE_DB`, `PULSE_WEBHOOK_URL`,
  `PULSE_TARGET_BASE`, `PULSE_SEED`.
- Verify: open `http://localhost:8001/`; break a service with
  `curl -X POST localhost:7003/control/down`; watch incident + webhook at `:9900`.

## Future improvements

- **Flap suppression** (analysed in `DECISIONS.md`): reopen-window so rapid down/up
  cycles extend one incident instead of creating new ones.
- Operator authentication; per-tenant views.
- Non-HTTP checks (TCP/DB) and richer notification targets (email/chat routing).
- Customer subscriptions to status updates.
- Postgres + separate worker process for multi-instance scale.
