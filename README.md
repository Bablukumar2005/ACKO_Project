# Pulse — a status and incident hub

Pulse continuously checks the health of Acko's services, notices when one is degraded
or down, opens and tracks an incident until recovery, keeps engineers/support/customers
informed, and proves reliability against each service's target.

Built for the **Acko DevOps Intern Product Build** (Option 1: Pulse).

## What it does

| Must-have | Where |
|---|---|
| 1. Service catalogue (API + UI) | `POST/GET/PATCH/DELETE /api/services`, dashboard form |
| 2. Health checking, isolated per service | one asyncio task per service, every result stored |
| 3. Service state (healthy/degraded/down) | pure state machine, thresholds per service |
| 4. Incidents with ack/notes/timeline | auto-open on down, auto-resolve on recovery |
| 5. Reliability vs target + error budget | 1 h / 24 h / 7 d windows on each service page |
| 6. Operator dashboard | `/` |
| 7. Public status page (sanitized) | `/status` |
| 8. Webhook notifications (open + resolve) | to the kit's receiver on :9900, logged |
| 9. Product metrics (MTTD, MTTR, checker perf, delivery) | `/metrics`, `/api/metrics` |
| 10. Runnable with one command | see below |

**Customers never see internal errors.** The kit's mock services return
`upstream …-db timeout at 10.12.4.7:5432` when down; Pulse classifies failures into
safe tokens (`timeout`, `conn_refused`, `http_5xx`, …) and the public status page and
webhook payloads render only plain-English text. Raw detail stays in the operator DB.

## Run it from scratch

Prereqs: Python 3.12+ and Docker (for the starter kit).

```bash
# 1. Start the starter kit (4 mock services + webhook receiver)
cd <path-to>/pulse-starter-kit
docker compose up --build -d

# 2. Start Pulse
cd <path-to>/pulse
pip install -r requirements.txt
python run.py
```

Then open:

| Page | URL |
|---|---|
| Operator dashboard | http://localhost:8001/ |
| Public status page | http://localhost:8001/status |
| Metrics | http://localhost:8001/metrics |
| Example incident | from the dashboard, click any incident |
| Kit webhook receiver | http://localhost:9900/ |

Pulse seeds the kit's four services (`quotes`, `payments`, `claims`, `notifications`,
checked every 5 s against `localhost:7001–7004`) on first run. Register your own with
the dashboard form or `POST /api/services`.

### In Docker instead

```bash
docker compose up --build     # Pulse on :8000; targets host.docker.internal
```

### Environment variables (all optional)

| Var | Default | Meaning |
|---|---|---|
| `PULSE_PORT` | `8001` | web port (8000 is a common clash on dev laptops) |
| `PULSE_DB` | `pulse.db` | SQLite file |
| `PULSE_WEBHOOK_URL` | `http://localhost:9900` | default notification target |
| `PULSE_TARGET_BASE` | `http://localhost` | base for seeded kit services |
| `PULSE_SEED` | `1` | set `0` to start with an empty catalogue |

# Try it (60-second demo)

```bash
curl -X POST localhost:7003/control/down        # break claims
#  dashboard turns yellow → red, incident opens, webhook receiver shows the alert
curl -X POST localhost:7003/control/reset       # heal claims
#  incident auto-resolves, webhook shows the recovery, MTTR recorded
curl -X POST "localhost:7002/control/slow?ms=3000"   # payments slow → degraded
curl -X POST "localhost:7001/control/flaky?rate=0.3" # quotes flaky → degraded
bash <path-to-kit>/scripts/incident-drill.sh --fast   # full repeatable drill
```

Or run the whole acceptance suite: `python -m pytest tests -q -m acceptance`
(kit + Pulse running).

## Architecture (one app + SQLite)

```
mock services ──► Scheduler (asyncio, 1 task/service) ──► Checker (httpx, per-request timeout)
                        │                                     │
                        │                                every result stored
                        ▼                                     ▼
                 Incident engine ◄── pure state machine (healthy|degraded|down)
                        │
                        ├─► Notifier (webhook POST, 3 attempts, logged)
                        └─► Reliability (uptime vs target, error budget) ──► Metrics (MTTD/MTTR)

FastAPI: JSON API (/api/…) + server-rendered UI (/, /status, /metrics)
SQLite: services, checks, incidents, incident_events, notifications
```

- **One slow service never delays the others** — each service checks in its own task;
  the checker enforces a per-request timeout (default 4 s, configurable).
- **State rules** (defaults, per-service in the catalogue): 3 consecutive failures →
  `down` → incident opens; 2 consecutive in-budget successes → `healthy` → incident
  resolves; anything else (1–2 failures, slow responses, flaky mixes) → `degraded`.
- **Restart-safe**: on boot, current states are re-derived from stored checks, so a
  Pulse restart doesn't re-open resolved incidents.

## Tests

```bash
python -m pytest tests -q                  # unit + integration (no Docker needed)
python -m pytest tests -q -m acceptance    # acceptance: needs the kit + Pulse running
bash scripts/acceptance.sh                 # POSIX alternative (macOS/Linux)
```

- **Unit** (34): state machine (fresh data, flaky, slow, recovery), reliability/error
  budget math, sanitizer guarantees, webhook payload shape, API contract.
- **Integration** (8): real asyncio + stub kit/webhook: down→incident→webhook,
  recovery→resolve→webhook, ack/note timeline, every check stored, timeout classes.
- **Acceptance** (9 tests, 18 checks): the full story against the real starter kit —
  down→incident→sanitized webhook, recovery→resolve→webhook, slow/flaky→degraded
  without incidents, ack/note/resolve via API, and "the status page never contains the
  kit's internal error text" (`10.12.4.7`).

## API summary

```
GET/POST        /api/services            catalogue (POST validates; 409 on duplicate name)
GET/PATCH/DELETE /api/services/{id}      detail includes reliability + incidents
GET             /api/status              sanitized public status (mirrors /status)
GET             /api/incidents?state=    open|acknowledged|resolved
GET             /api/incidents/{id}      full timeline
POST            /api/incidents/{id}/ack | /notes | /resolve
GET             /api/metrics             MTTD, MTTR, checker perf, delivery rate
```

## What works / what doesn't

Works: everything in the table at the top, verified by 51 unit/integration tests and
9 acceptance tests against the real starter kit. Doesn't (yet): operator login,
non-HTTP checks, customer subscriptions, AI summaries — deliberate scope cuts, see
`PLAN.md`/`DECISIONS.md`.

## Project docs

- `PLAN.md` — Gate 1 plan (unchanged after the gate)
- `DECISIONS.md` — timestamped decision log
- `PROGRESS.md` — Gate 2 progress note
- `REVIEW.md` — self-review: what works, what the AI got wrong
- `transcripts/` — AI coding sessions (required by the assessment)
