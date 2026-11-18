# PLAN.md — Pulse, a status and incident hub

> Gate 1 plan (written at ~1:00 into the build). Left unchanged afterwards; later
> changes are recorded in `DECISIONS.md`, as the problem statement asks.

## The problem, in my own words

Acko runs hundreds of services. When one breaks, three groups are hurt and today
nobody tells them in time:

- **Engineers on call** — need to know *now*, so they can start fixing.
- **The support team** — start getting customer calls and need to know what to say.
- **Customers** — see a payment fail and don't know if the problem is theirs or ours.

Afterwards nobody can answer "how reliable has this service actually been, and is it
meeting its target?".

**Pulse** continuously checks the health of registered services, notices when one is
degraded or down, opens and tracks an incident until recovery, keeps the team and
customers informed (operator dashboard + public status page + webhook notifications),
and proves reliability against each service's target.

**Outcome that matters:** bad minutes turn into known incidents in seconds, with a
timeline everyone can read — and a number the team can be held to.

## Questions I asked the product owner (and answers/assumptions)

| # | Question | Answer / assumption |
|---|---|---|
| Q1 | Does a *degraded* service (slow or flaky) open an incident, or only "down"? | Pending → **assumption**: only `down` opens an incident; degradation is shown on the dashboard and added to the incident timeline if one is open. Rationale: paging someone for a 2-minute slow patch is noisy; but degraded state must still be *visible*. |
| Q2 | What should the public status page show when a service is down? | Pending → **assumption**: plain-English status ("Not working right now — we're on it"), **never** internal error text. The kit deliberately plants internal error strings (`upstream claims-db timeout at 10.12.4.7:5432`); leaking them to customers is the trap. |
| Q3 | Default check interval and reliability target? | Pending → **assumption**: every 5 s, target 99.9%, both editable per service in the catalogue. |
| Q4 | How many failed checks before we call it "down"? | Pending → **assumption**: 3 consecutive (≈15 s at default interval) to down; 2 consecutive successes to recover. Avoids paging on one blip. |
| Q5 | Who can ack/resolve — do we need login? | Pending → **assumption**: no login in this version (single-operator tool); recorded as a deliberate scope cut. |
| Q6 | Where do notifications go? | The starter kit's webhook receiver stands in for chat/email; payload format is my choice → JSON with `event`, `service`, `incident_id`, timestamps, sanitized `summary`. |

*(Owner answers will be appended to the decision log as they arrive; every "your call"
above is also recorded in `DECISIONS.md` with a timestamp.)*

## What I learned from real products

- **Atlassian Statuspage** (public status pages): customers get plain-English component
  status + a history of past incidents; internal detail never appears publicly. → I copy
  the "sanitized public surface" rule and the incident-history pattern.
- **Uptime Kuma** (self-hosted monitoring): per-monitor check intervals/timeouts,
  heartbeat storage, "down × N flips state" logic. → I copy per-service schedules and
  consecutive-failure thresholds.
- **Healthchecks.io**: every check attempt is stored, and SLA windows are computed from
  raw attempts. → I store every check result (not just transitions) so reliability and
  metrics are computed from data, not fabricated.
- **PagerDuty incident model**: incidents have a lifecycle (triggered → acknowledged →
  resolved) and an immutable timeline of events. → I copy open → ack → resolve + events.

## What I will build (must-haves first) and what I leave out

**In scope (must-haves 1–10, in build order):**

1. Service catalogue (API + UI), seeded with the kit's 4 mock services.
2. Health checking: one asyncio task per service (own interval/timeout), every result stored.
3. Service state: `healthy / degraded / down / unknown` from a pure state-machine function.
4. Incidents: auto-open on down, ack + notes, auto-resolve on recovery, timeline events.
5. Reliability: uptime % over 1 h / 24 h / 7 d vs target + error-budget remaining.
6. Operator dashboard: states, latency, incidents, timelines, actions.
7. Public status page: read-only, sanitized.
8. Notifications: webhook POST on open + resolve, logged with retries.
9. Product metrics: MTTD, MTTR, check success/latency, notification delivery rate.
10. Runnable: kit `docker compose up` + `python run.py` (or Pulse in Docker), README.

**Out of scope (with reasons):**

- Login/auth for the operator view — single-user demo tool; time better spent on tests.
- Non-HTTP checks (TCP/DB) — the kit only offers HTTP health endpoints.
- Customer subscriptions to status updates — needs a real email/chat provider.
- Kubernetes/multi-instance deployment — laptop Docker is explicitly sufficient.
- AI-written incident summaries — only if everything else is done and verified.

## System design sketch

```
                 ┌────────────────────────── Pulse app ──────────────────────────┐
                 │                                                               │
 mock services ──┼─► Scheduler: one asyncio task per service (isolated checks)   │
 :7001–7004      │      └► Checker (httpx GET /health, per-request timeout)      │
                 │          └► State machine (pure function over recent checks)  │
                 │              ├► Incident engine (open/ack/notes/resolve)      │
                 │              ├► Reliability calc (windows vs target, budget)  │
                 │              └► Notifier (webhook POST, retries, logged)      │
                 │                                                               │
                 │   FastAPI: JSON API + server-rendered UI (Jinja2)             │
                 └──────────────────────────── SQLite ───────────────────────────┘
```

Key choices: single process + SQLite (one command to run, zero infrastructure);
state machine is a **pure function** `(recent_results, thresholds) → state` so it is
unit-testable without Docker; every check stored; one sanitizer choke-point so internal
error text can never reach the public page.

## Data model

```
services(id, name UNIQ, health_url, interval_s, timeout_s, degraded_latency_ms,
         failures_to_down, successes_to_recover, owner, target_pct, webhook_url,
         enabled, created_at)
checks(id, service_id→services CASCADE, ts, ok, status_code, latency_ms,
       error_class,            -- safe token: timeout | conn_refused | http_5xx | …
       error_detail)           -- INTERNAL ONLY: raw message, never leaves /api/operator surfaces
incidents(id, service_id→services CASCADE, opened_at, resolved_at, acked_at,
          state open|acknowledged|resolved, detected_after_s)   -- MTTD captured at open
incident_events(id, incident_id→incidents CASCADE, ts, kind, message)  -- append-only timeline
notifications(id, incident_id→incidents SET NULL, event, target_url, payload,
              response_code, ok, attempts, ts)
```

## Main screens

1. `/` Operator dashboard — table of services (state badge, last latency, uptime 24 h vs
   target), current incidents; auto-refresh.
2. `/services/{id}` — config, reliability windows + error budget, last checks, incidents.
3. `/incidents/{id}` — timeline, Ack button, note form, Resolve button.
4. `/status` — public page: big per-service status cards, recent history; sanitized.
5. `/metrics` — MTTD, MTTR, checker performance, notification delivery.

## Test plan

- **Unit** (no network, no Docker): state-machine transitions (fresh data, flaky mixes,
  slow latency, recovery streaks); reliability/error-budget math; sanitizer guarantees;
  webhook payload shape (no internal detail); catalogue validation.
- **Integration** (in-process, real asyncio): a stub HTTP service that can go
  down/slow/flaky on demand + a stub webhook → run the real checker→state→incident→
  notifier pipeline and assert DB rows, webhook payloads, retry behaviour.
- **Acceptance** (against the real starter kit, skipped if the kit isn't running):
  `control/down` on claims → incident opens + webhook receiver shows the notification;
  `control/reset` → incident resolves; the public status output never contains the kit's
  planted internal error string; `incident-drill.sh --fast` as the demo script.

## Metrics that will show the product works

- **MTTD** — time from last good check to incident opened (target: < 1 check-interval × failures_to_down).
- **MTTR** — incident open → resolved.
- Check success rate + check latency (is the checker itself healthy?).
- Notification delivery rate (did the support team get told?).
- Uptime % vs target per service with error-budget remaining.
