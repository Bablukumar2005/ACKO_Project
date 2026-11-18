# PROJECT_PLAN_V2.md — Pulse project plan (v2)

> The filename `PROJECT_PLAN.md` was already taken (existing file preserved
> untouched), so this updated plan lives here. Original Gate 1 goals remain in
> `PLAN.md`; the implementation reference is `DESIGN.md`; decisions and their
> rationale are in `DECISIONS.md`.

## Project overview

Pulse is a status and incident hub for Acko's mock microservices (the starter kit's
`quotes`, `payments`, `claims`, `notifications`). It health-checks each registered
service every few seconds, classifies it `healthy / degraded / down`, opens and
tracks an incident until recovery, alerts a webhook receiver, and reports
reliability against per-service targets — with an operator dashboard, a sanitized
public status page, and (added after the main build) secure operator login.

## Objectives

All objectives below are **implemented and verified** (evidence in parentheses):

1. Continuously monitor registered services with fault isolation between checks
   (one asyncio task per service; every result stored).
2. Classify service state deterministically — 3 consecutive failures → `down`,
   2 confirmed consecutive successes → `healthy`, otherwise `degraded`
   (pure-function state machine, unit-tested).
3. Track incident lifecycle: auto-open on down, auto-resolve on recovery, ack /
   notes / manual resolve, append-only timeline (integration + acceptance tested).
4. Notify teams: sanitized `incident_opened` / `incident_resolved` webhooks, 3
   attempts with backoff, every attempt logged (delivery rate in `/api/metrics`).
5. Prove reliability: 1h/24h/7d uptime % vs per-service target with error-budget
   arithmetic (check-weighted math, unit-tested).
6. Serve three audiences: engineers (dashboard + API), support (webhooks),
   customers (sanitized public status page — internal error text can never leak,
   enforced at the checker choke-point and asserted by tests).
7. Secure operator surfaces with login (bcrypt accounts, session cookies,
   admin-created users) without gating public reads.
8. Run with one command on a laptop (`python run.py`); Docker-on-laptop scale only.

## Architecture

```
              ┌────────────────────────── Pulse (one process) ─────────────────────┐
 mock services │ Scheduler: 1 asyncio task per service (isolated)                  │
 :7001–7004 ──►        └► Checker (httpx GET /health → error classes)              │
              │            └► State machine (pure fn: 3 fails→down, 2 ok→recover)   │
              │                ├► Incidents (open/ack/note/resolve + timeline)      │
              │                ├► Notifier (webhook, 3 attempts, logged)            │
              │                ├► Reliability (uptime % vs target, error budget)    │
              │                └► Metrics (MTTD, MTTR, checker p50/95/99, delivery) │
              │ FastAPI: JSON API (/api/*) + Jinja2 UI + session auth               │
              └──────────────────────────────────┬─────────────────────────────────┘
                                                 ▼
                          SQLite (WAL): services, checks, incidents,
                          incident_events, notifications, users
```

Full component, data-flow, database and API design: see `DESIGN.md`.

## Technology stack

- **Python 3.12** · FastAPI + Uvicorn (web/API) · httpx (async checks + webhooks)
- **Jinja2** server-rendered templates — no JS build step; meta-refresh live pages
- **SQLite** in WAL mode, single guarded connection, reentrant-lock transactions
- **bcrypt** password hashing · **itsdangerous** signed session cookies
- **pytest + pytest-asyncio** — unit / integration / acceptance layers
- **Docker Compose** — runs the starter kit (and optionally Pulse itself)

## Implementation phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Scaffold: repo, plan, decision log, schema, store layer | ✅ done |
| 2 | Core logic: checker, pure state machine, unit tests | ✅ done |
| 3 | Runtime: scheduler, incident engine, notifier, kit seeding | ✅ done |
| 4 | Web & API: dashboard, status page, metrics, JSON API, reliability | ✅ done |
| 5 | Verification: integration suite (stub kit/webhook) + acceptance vs real kit | ✅ done |
| 6 | Docs & handover: README/REVIEW/PROGRESS, transcripts, acceptance suite | ✅ done |
| 7 | Operator login: users table, bcrypt auth, sessions, route guards | ✅ done (added post-gate) |
| 8 | Planned, not started: flap suppression, roles in UI, non-HTTP checks | ⬜ future |

## Future improvements (planned — not implemented)

- **Flap suppression**: reopen-window so rapid down/up cycles extend one incident
  instead of creating new ones — fully analysed and quantified in `DECISIONS.md`.
- Role-aware UI (admin managing accounts from a page, not just the API).
- Non-HTTP checks (TCP/database probes) and richer notification targets
  (email / chat routing rules).
- Customer subscriptions to status updates.
- Postgres + a separate worker process if scale ever outgrows one laptop.
