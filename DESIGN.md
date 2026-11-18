# DESIGN.md — how Pulse is built

> Design reference for the implemented system. Goals live in `PLAN.md` (Gate 1,
> unchanged), the consolidated plan in `PROJECT_PLAN.md`, and every post-gate
> change with its rationale in `DECISIONS.md`.

## System architecture

One Python process. FastAPI serves both the JSON API and the server-rendered UI;
an asyncio scheduler owns one isolated task per service; all state lives in a
single SQLite database.

```
            ┌───────────────────────────── Pulse process ─────────────────────────────┐
            │                                                                        │
 mock kit   │  Scheduler (asyncio, 1 task/service)                                   │
 :7001–7004 │    └─ Checker (httpx GET /health, per-request timeout)                  │
            │        └─ record check (SQLite)                                        │
            │            └─ State machine (pure fn, per-service thresholds)           │
            │                ├─ Incident engine (open/ack/note/resolve, timeline)     │
            │                │    └─ Notifier (webhook POST, 3 attempts, background)  │
            │                ├─ Reliability windows (1h/24h/7d, error budget)         │
            │                └─ Metrics (MTTD, MTTR, checker percentiles, delivery)   │
            │                                                                        │
 operator ──►  FastAPI web routes (Jinja2)   JSON API (/api/*)   session auth          │
 customers ─►  /status (sanitized public)    bcrypt users table                        │
            │                                                                        │
            └───────────────────────────────────┬────────────────────────────────────┘
                                                ▼
                        SQLite (WAL): services, checks, incidents,
                        incident_events, notifications, users
```

## Component descriptions

| Module | File | Responsibility |
|---|---|---|
| Config | `pulse/config.py` | All env knobs with defaults (`PULSE_PORT=8001`, `PULSE_DB`, `PULSE_WEBHOOK_URL`, `PULSE_TARGET_BASE`, `PULSE_SEED`, `PULSE_ADMIN_EMAIL/PASSWORD`, `PULSE_SECRET`) |
| DB layer | `pulse/db.py` | Schema (idempotent `CREATE TABLE IF NOT EXISTS`), one process-wide SQLite connection in WAL mode, reentrant-lock write transactions |
| Store | `pulse/store.py` | Every query for monitoring tables; no SQL elsewhere |
| Users | `pulse/users.py` | `users` table access + bcrypt hashing/verification; bootstrap admin creation |
| Auth | `pulse/auth.py` | Session helpers + FastAPI dependencies: `require_api_user` (401/403), `require_web_user` (303 to `/login`) |
| Checker | `pulse/checker.py` | HTTP health check with per-request timeout; classifies errors (`timeout`, `conn_refused`, `http_5xx`, …); holds the **sanitizer choke-point** — `error_class` is public, raw `error_detail` internal-only |
| State machine | `pulse/state.py` | Pure function: 3 consecutive failures → `down`, 2 consecutive in-budget successes → `healthy`, else `degraded`; after a full failure-run recovery requires confirmed consecutive successes (no single-lucky-success flips) |
| Scheduler | `pulse/scheduler.py` | One asyncio task per service (isolated intervals/timeouts); syncs on catalogue changes; re-derives state on restart |
| Incident engine | `pulse/incidents.py` | Opens on `down` (only), auto-resolves on recovery, ack/notes/manual resolve, append-only `incident_events` timeline |
| Notifier | `pulse/notifier.py` | `incident_opened` / `incident_resolved` payloads per service webhook URL; 3 attempts with backoff in a background task; every attempt logged in `notifications` |
| Reliability | `pulse/reliability.py` | Check-weighted uptime % over 1h/24h/7d vs per-service target; error budget = window × (1 − target), used from failure ratio |
| Metrics | `pulse/metrics.py` | MTTD/MTTR from incident records, checker success rate + p50/p95/p99 latency, notification delivery rate |
| Seed | `pulse/seed.py` | Registers the kit's 4 services (quotes/payments/claims/notifications → :7001–7004) on an empty DB; `PULSE_SEED=0` disables |
| App wiring | `pulse/main.py` | Lifespan (connect DB → bootstrap admin → seed → start scheduler), `SessionMiddleware`, router mounting |
| JSON API | `pulse/api/routes.py` | Pydantic-validated catalogue CRUD, status, incidents, metrics, auth endpoints |
| Web UI | `pulse/web/routes.py` + `templates/` | Operator dashboard, service/incident detail, metrics page, public status page, login page |
| Entry point | `run.py` | `python run.py` → uvicorn on `127.0.0.1:8001` |

## Data flow

**Check cycle (every service, every interval):**
scheduler task fires → `checker` does `GET /health` with per-request timeout →
result stored in `checks` (ok, status_code, latency_ms, error_class, error_detail) →
`state.py` computes the next state from consecutive-failure/success counters →
on transition: `incident_events` gets a `detail` entry; on `down`: incident opened
(+ webhook, best-effort); on confirmed recovery: incident resolved (+ webhook).

**Incident lifecycle:** `open` → (ack → `acknowledged`; notes appended at any time) →
`resolved` (auto on recovery or manual). Timeline kinds: `opened`, `detail`,
`notified`, `acked`, `note`, `resolved`. `detected_after_s` (MTTD) and open→resolved
span (MTTR) are computed from these records.

**Notification flow:** engine → `Notifier.notify()` (background) → up to 3 POSTs to
the service's webhook URL (default the kit receiver :9900) → each attempt recorded in
`notifications` (payload, response_code, ok, attempts). Payloads contain only
sanitized fields — the kit's planted internal error text (`10.12.4.7`) can never
appear, enforced at the checker choke-point and verified by tests.

**Login flow:** `POST /login` (form or `/api/login` JSON) → email lookup →
`bcrypt.checkpw` → signed session cookie (itsdangerous, 24 h max-age) → guarded
routes resolve `uid` → `users` row. Anonymous operator access: API mutations get 401,
admin-only endpoints 403, operator pages 303 → `/login?next=<path>` (same-site paths
only, no open redirects).

## Database design

SQLite, WAL mode, foreign keys ON, single shared connection guarded by a
`threading.RLock` (all writes via `db.tx(write=True)` — helpers may nest).

| Table | Key columns | Notes |
|---|---|---|
| `services` | id, name UNIQUE, health_url, interval_s, timeout_s, degraded_latency_ms, failures_to_down, successes_to_recover, owner, target_pct, webhook_url, enabled, created_at | CHECK constraints bound thresholds; per-service tuning |
| `checks` | service_id FK→services CASCADE, ts, ok, status_code, latency_ms, error_class, error_detail | indexed (service_id, ts); raw `error_detail` never rendered publicly |
| `incidents` | service_id FK, opened_at, resolved_at, acked_at, state CHECK(open/acknowledged/resolved), detected_after_s | one open incident per service at a time |
| `incident_events` | incident_id FK CASCADE, ts, kind, message | append-only timeline, indexed (incident_id, id) |
| `notifications` | incident_id FK SET NULL, event, target_url, payload, response_code, ok, attempts, ts | delivery audit log |
| `users` | id, name, email UNIQUE, password_hash, role CHECK(admin/operator/viewer), created_at | added for operator login; bcrypt hashes only |

Schema changes are additive: new tables are `CREATE TABLE IF NOT EXISTS` entries in
`db.SCHEMA`, applied idempotently at startup — existing tables and data are never
touched (verified when `users` was added).

## API design

JSON under `/api` (Pydantic-validated; auth column shows the session requirement):

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/login` | — | email+password → session cookie; 401 on bad credentials |
| `POST /api/logout` | — | clear session |
| `GET /api/me` | login | current user (never the hash) |
| `POST /api/users` | **admin** | create account (password ≥ 8 chars, role-validated); 409 on duplicate email |
| `GET /api/services` | — | catalogue with live state + friendly error text |
| `POST /api/services` | login | create service (full validation; 409 on duplicate name) |
| `GET/PATCH/DELETE /api/services/{id}` | GET open; rest login | detail + reliability card; partial update; delete |
| `GET /api/status` | — | sanitized customer-facing view |
| `GET /api/incidents` · `/{id}` | — | list (filter by state) / detail with timeline |
| `POST /api/incidents/{id}/ack` · `/notes` · `/resolve` | login | operator actions |
| `GET /api/metrics` | — | incidents, detection/resolution, checker, notifications |

Web routes: `/` dashboard, `/services/{id}`, `/incidents/{id}` (+ form posts for
ack/note/resolve), `/status`, `/metrics`, `/login` — the same auth split (public:
status/metrics/login; operator pages 303 to login when anonymous).

Error semantics: 401 unauthenticated · 403 non-admin · 404 unknown id ·
409 conflict (duplicate name/email) · 422 validation.

## Technology choices

- **FastAPI + Uvicorn** — typed request models give free validation; one framework
  serves JSON and server-rendered pages.
- **One asyncio task per service** — a hung check can't stall other services;
  per-service interval/timeout are independent.
- **Pure state-machine function** — every transition is unit-testable without HTTP,
  DB, or asyncio.
- **SQLite (WAL) + single guarded connection** — zero-admin persistence, safe
  concurrent readers, serialized writers via `RLock`; right size for
  Docker-on-laptop scale (no Kubernetes per constraints).
- **Jinja2 server-rendered UI, no JS framework** — meta-refresh live pages, zero
  build step; auto-refresh only where promised (5 s dashboard, 10 s status).
- **httpx (async)** — checker and notifier share one client; per-request timeouts.
- **bcrypt + itsdangerous SessionMiddleware** — industry-standard password hashing
  with per-user salts; signed cookie sessions without a token/DB session store.
- **pytest + pytest-asyncio** — 46 unit, 8 integration (stub kit + webhook), 9
  acceptance against the real kit (`pytest -m acceptance`, auto-skipped when the kit
  is down).

**Known limitation (documented, not hidden):** a rapidly flapping service opens a new
incident per down/up cycle — quantified with the planned reopen-window fix in
`DECISIONS.md`.
