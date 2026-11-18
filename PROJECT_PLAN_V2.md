# PROJECT_PLAN_V2.md — Pulse project plan

> Living plan for the Pulse project. Companion documents:
> [DESIGN.md](DESIGN.md) (how the system works internally) ·
> [PROGRESS_STATUS.md](PROGRESS_STATUS.md) (what is done, tested and working) ·
> [README_PRODUCT.md](README_PRODUCT.md) (install, run, login, use).
> The original buildathon gate-1 goals are preserved unchanged in `PLAN.md`;
> the earlier consolidated snapshot `PROJECT_PLAN.md` is kept as-is and is
> superseded by this file. Decision rationale lives in `DECISIONS.md`.

## Project overview

Pulse is a status and incident hub for Acko's mock microservices — the starter
kit's `quotes`, `payments`, `claims` and `notifications` containers. It
health-checks each registered service every few seconds, classifies it
`healthy / degraded / down` with a deterministic state machine, opens and
tracks an incident until recovery, alerts a webhook receiver, and reports
reliability against per-service targets. It serves three audiences at once:

- **Engineers** — operator dashboard, service/incident timelines, product
  metrics, full JSON API.
- **Support teams** — sanitized `incident_opened` / `incident_resolved`
  webhooks with delivery audit.
- **Customers** — a sanitized public status page that can never leak internal
  error text.

Operator surfaces are protected by login (bcrypt accounts in the same SQLite
database) with public self-service registration; public status pages and all
read-only APIs stay open.

## Objectives

All objectives below are **implemented and verified** — evidence and test
coverage are tracked in [PROGRESS_STATUS.md](PROGRESS_STATUS.md#completed-features),
internals in [DESIGN.md](DESIGN.md#system-architecture):

1. Continuously monitor registered services with fault isolation between
   checks — one asyncio task per service, every attempt stored.
2. Classify service state deterministically — 3 consecutive failures →
   `down`, 2 confirmed consecutive successes → `healthy`, otherwise
   `degraded` — as a pure, unit-tested function with per-service thresholds.
3. Track the full incident lifecycle: auto-open on `down` only, auto-resolve
   on recovery, ack, notes, manual resolve, append-only timeline.
4. Notify support teams with sanitized webhooks — 3 attempts with backoff,
   every attempt logged and audited.
5. Prove reliability — check-weighted uptime over 1 h / 24 h / 7 d vs
   per-service target, with error-budget arithmetic.
6. Serve product metrics — MTTD, MTTR, checker success rate and latency
   percentiles, notification delivery rate.
7. Provide a sanitized customer-facing status page — internal error text can
   never reach public surfaces (enforced at the checker choke-point, asserted
   by dedicated tests).
8. Secure operator surfaces with login — bcrypt accounts, session cookies,
   self-service registration, admin-only role management — without gating
   public reads.
9. Run with one command on a laptop (`python run.py`, default port 8001);
   Docker-on-laptop scale only, per the buildathon constraints.

## Requirements

### Functional requirements (implemented)

| # | Requirement | Where |
|---|---|---|
| F1 | Register services with URL, interval, timeout, thresholds, owner, target, webhook | `store.py`, API + dashboard form |
| F2 | Health-check each enabled service on its own schedule | `scheduler.py`, `checker.py` |
| F3 | Classify state from recent results (unknown/healthy/degraded/down) | `state.py` |
| F4 | Open/resolve incidents automatically; ack/notes/manual resolve | `incidents.py` |
| F5 | Webhook notifications, sanitized, retried, audited | `notifier.py` |
| F6 | Reliability windows + error budget vs target | `reliability.py` |
| F7 | Product metrics (MTTD, MTTR, checker, delivery) | `metrics.py` |
| F8 | Operator dashboard, incident pages, metrics page, public status | `web/routes.py` |
| F9 | JSON API for everything above | `api/routes.py` |
| F10 | Operator accounts: self-registration (public), login, logout, admin-created accounts | `users.py`, `auth.py` |
| F11 | Route protection: pages redirect to login, mutations 401, admin-only 403 | `auth.py` guards |

### Non-functional requirements (implemented)

| # | Requirement | How |
|---|---|---|
| N1 | Never expose internal error text publicly | sanitizer choke-point in `checker.py` + payload builder |
| N2 | Never store or log plain-text passwords | bcrypt hashes only; passwords never re-rendered; log-hygiene tests |
| N3 | A hung service must not stall monitoring | isolated asyncio task per service |
| N4 | A restart must not manufacture incidents | state re-derivation from stored checks at startup |
| N5 | Schema upgrades must be additive and idempotent | `CREATE TABLE IF NOT EXISTS` only; verified when `users` was added |
| N6 | One-command startup with sensible defaults | `python run.py`, seeded kit services, port 8001 |

## Architecture

Summary diagram and full component/data-flow/database/API design:
[DESIGN.md → System architecture](DESIGN.md#system-architecture). In one
sentence: **one Python process** — FastAPI (JSON API + Jinja2 UI + session
auth) around an asyncio scheduler (one task per service) whose results flow
through a pure state machine into an incident engine and notifier, all backed
by a single SQLite (WAL) database.

## Technology stack

- **Python 3.12** · FastAPI + Uvicorn · httpx (async checks + webhooks)
- **Jinja2** server-rendered templates — no JS build step; tiny vanilla JS for
  password visibility and live signup feedback
- **SQLite** in WAL mode — single guarded connection, reentrant-lock
  transactions
- **bcrypt** password hashing · **itsdangerous** signed session cookies
- **pytest + pytest-asyncio** — unit / integration / acceptance layers
- **Docker Compose** — runs the starter kit; an optional compose file runs
  Pulse itself ([README_PRODUCT.md → Docker setup](README_PRODUCT.md#docker-setup))

## Implementation phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Scaffold: repo, gate-1 plan, decision log, schema, store layer | ✅ done |
| 2 | Core logic: checker, pure state machine, unit tests | ✅ done |
| 3 | Runtime: scheduler, incident engine, notifier, kit seeding | ✅ done |
| 4 | Web and API: dashboard, status page, metrics, JSON API, reliability | ✅ done |
| 5 | Verification: integration suite (stub kit + webhook) + acceptance vs real kit | ✅ done |
| 6 | Docs and handover: README/REVIEW/PROGRESS, transcripts | ✅ done |
| 7 | Operator login: users table, bcrypt auth, sessions, route guards | ✅ done |
| 8 | Registration UX: public sign-up page + API, validation, redesigned login, admin bootstrap without secrets in logs | ✅ done |
| 9 | Hardening backlog (below) | ⬜ planned — not started |

## Future roadmap

Everything below is **planned, not implemented** — none of it exists in the
current code. Order reflects current priority; rationale for the first item is
quantified in `DECISIONS.md`.

| Priority | Improvement | Problem it solves | Sketch |
|---|---|---|---|
| 1 | Flap suppression (reopen window) | A rapidly cycling service opens a new incident + webhook per down/up cycle | If a service recovers within N seconds of going down, extend/reopen the recent incident instead of creating a new one |
| 2 | Failure latency recording | Failed checks store `latency_ms = 0.0`, so latency percentiles read 0 ms during a full outage | Record elapsed time-to-failure in `checker._fail()` or compute percentiles over successful checks only |
| 3 | Error-budget window scaling | Budget "used" is extrapolated across a nominal window even when the DB holds less history | Scale the window to the observed data span, or label partially-filled windows |
| 4 | Password management | No self-service password reset today | Change-password page for the signed-in user; admin reset action |
| 5 | Role-aware UI | Admins manage accounts only via `POST /api/users` | Users-management page in the dashboard |
| 6 | Non-HTTP checks | Only HTTP GET health probes are supported | TCP/database probe types in the checker |
| 7 | Richer notification targets | Single webhook URL per service | Email / chat routing rules |
| 8 | Customer subscriptions | Public status page is read-only for visitors | Opt-in status-update notifications |
| 9 | Beyond one laptop | Single-process SQLite on one host | Postgres + separate worker process, only if scale ever demands it |

Also outstanding on the process side (not code): commit/push approval for the
current working tree and a transcript re-export — tracked in
[PROGRESS_STATUS.md → Pending](PROGRESS_STATUS.md#pending).
