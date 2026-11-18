# README_PRODUCT.md — Pulse product guide

> The filename `README.md` was already taken (existing deliverable preserved
> untouched), so this product guide lives here. For design depth see `DESIGN.md`;
> for the plan see `PROJECT_PLAN_V2.md`.

## Introduction

Pulse is a status and incident hub for Acko's mock microservices. It continuously
health-checks each registered service, notices degradation and outages in seconds,
opens an incident with a readable timeline, alerts a webhook receiver, and proves
reliability against per-service targets. Engineers get an operator dashboard,
support gets webhook alerts, customers get a sanitized public status page — internal
error text (the kit's planted `10.12.4.7` string) can never reach public surfaces.

## Features (all implemented)

- **Service catalogue** — register/edit via API or dashboard form; pre-seeded with
  the kit's `quotes`, `payments`, `claims`, `notifications`.
- **Isolated health checking** — one asyncio task per service, per-request timeout,
  every attempt stored (latency, status, error class).
- **Deterministic state machine** — `unknown / healthy / degraded / down`; 3
  consecutive failures → down, 2 confirmed consecutive successes → recovery;
  slow-but-up services degrade instead of failing.
- **Incident lifecycle** — auto-open on down, auto-resolve on recovery, one-click
  ack, notes, manual resolve, append-only timeline, MTTD/MTTR recorded.
- **Webhook notifications** — `incident_opened` / `incident_resolved` payloads, 3
  attempts with backoff, every attempt logged (visible per incident).
- **Reliability & error budget** — 1h/24h/7d uptime % vs target, budget used/left.
- **Product metrics** — `/metrics` page + `/api/metrics`: MTTD, MTTR, checker
  success rate and p50/p95/p99 latency, webhook delivery rate.
- **Public status page** — plain-English states, auto-refreshes every 10 s,
  sanitized by construction.
- **Operator login** — bcrypt-hashed accounts in the existing SQLite database,
  signed session cookies, first admin auto-created at startup, admin-created users,
  protected operator pages/actions with public reads left open.

Planned but **not** implemented: flap suppression, role-aware UI, non-HTTP checks,
customer subscriptions (see `PROJECT_PLAN_V2.md`).

## Technology stack

Python 3.12 · FastAPI + Uvicorn · httpx (async) · Jinja2 (server-rendered, no JS
build) · SQLite (WAL, single guarded connection) · bcrypt + itsdangerous sessions ·
pytest + pytest-asyncio · Docker Compose (starter kit; optional Pulse container).

## Installation and setup

Prerequisites: Python 3.12+, Docker Desktop (for the mock kit).

```bash
# 1. Start the mock kit (4 services :7001–7004, webhook receiver :9900)
cd pulse-starter-kit && docker compose up --build -d

# 2. Install and run Pulse (default port 8001)
cd pulse
pip install -r requirements.txt
python run.py
```

Environment variables (all optional):

| Var | Default | Meaning |
|---|---|---|
| `PULSE_PORT` | `8001` | web port (8000 clashes with Docker Desktop on many laptops) |
| `PULSE_DB` | `pulse.db` | SQLite path (login uses this same database) |
| `PULSE_WEBHOOK_URL` | `http://localhost:9900` | default webhook target |
| `PULSE_TARGET_BASE` | `http://localhost` | kit base URL used when seeding (inside Docker: `http://host.docker.internal`) |
| `PULSE_SEED` | `1` | set `0` to disable seeding the kit's services |
| `PULSE_ADMIN_EMAIL` | `admin@pulse.local` | bootstrap admin email |
| `PULSE_ADMIN_PASSWORD` | *(random, printed once to the log)* | first admin's password |
| `PULSE_SECRET` | *(random per process)* | session signing key; set it to keep logins across restarts |

## How to run

- **Laptop:** `python run.py` → open **http://localhost:8001** → log in with the
  bootstrap admin (email above; password from `PULSE_ADMIN_PASSWORD` or the log).
- **Container:** `docker compose up --build` in `pulse/` then open :8001.
- **60-second demo** (after login):
  ```bash
  curl -X POST localhost:7003/control/down          # break claims → incident + webhook
  curl -X POST localhost:7003/control/reset         # heal → auto-resolve + webhook
  curl -X POST "localhost:7002/control/slow?ms=3000" # degraded, no incident
  curl -X POST "localhost:7001/control/flaky?rate=0.3"
  ```
  Anonymous visitors hitting `/` are redirected to `/login`; `/status` and
  `/metrics` stay public.

## API endpoints

JSON under `/api` — reads are public; mutations require a login session;
`POST /api/users` requires the admin role.

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/login` | — | sign in (email + password) |
| `POST /api/logout` | — | sign out |
| `GET /api/me` | login | current user |
| `POST /api/users` | admin | create account (duplicate email → 409) |
| `GET /api/services` · `GET /api/services/{id}` | — | catalogue; detail with reliability card |
| `POST /api/services` · `PATCH/DELETE /api/services/{id}` | login | create / update / delete |
| `GET /api/status` | — | sanitized public status |
| `GET /api/incidents` · `/{id}` | — | list (optional `?state=`) / detail + timeline |
| `POST /api/incidents/{id}/ack` · `/notes` · `/resolve` | login | operator actions |
| `GET /api/metrics` | — | product metrics |

Errors: `401` unauthenticated · `403` non-admin · `404` unknown · `409` conflict ·
`422` validation. Interactive docs: `/docs` (FastAPI OpenAPI UI).

## Testing instructions

```bash
python -m pytest tests -q                  # 75 unit + integration tests, no kit needed
python -m pytest tests -q -m acceptance    # 9 acceptance tests; needs kit + Pulse up
python -u scripts/verify_nav.py            # live navigation check (logs in as admin)
python -u scripts/verify_auth.py           # 22-check live login/DB-integrity check
bash scripts/acceptance.sh                 # POSIX alternative (macOS/Linux)
```

The acceptance module auto-skips when the kit or Pulse isn't running, so the
default suite never fails on a laptop without Docker.
