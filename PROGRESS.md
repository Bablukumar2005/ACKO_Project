# PROGRESS.md — Gate 2 note (3:30 checkpoint)

## What works so far

- **Full pipeline live**: checker → state machine → incident engine → webhook
  notifier, running end-to-end against the starter kit (all four mock services
  registered and checked every 5 s).
- **Operator dashboard** (`/`): live states, uptime vs target, latency, current
  incidents with one-click ack/note/resolve, service registration form.
- **Public status page** (`/status`): sanitized (verified — internal error strings from
  the kit's down-mode never appear; covered by automated tests too).
- **Incidents**: auto-open on down (3 consecutive failures), auto-resolve on recovery
  (2 consecutive successes), ack + notes + immutable timeline, manual resolve.
- **Notifications**: `incident_opened` / `incident_resolved` JSON to the webhook
  receiver, 3 attempts with backoff, every attempt logged (visible per incident).
- **Reliability**: 1 h / 24 h / 7 d uptime % vs target + error budget arithmetic.
- **Metrics**: MTTD, MTTR, checker success rate + p50/p95/p99, notification delivery.
- **JSON API**: catalogue CRUD (validated), status, incidents, metrics.
- **Tests**: 51 automated (unit + integration, no Docker needed) — all green; the
  integration suite uses a stub kit/webhook to prove the whole pipeline, including
  "webhook payload must not contain internal error text".

## What's next

1. ~~Run the acceptance suite against the real kit~~ **Done: 9/9 passed** (down→
   incident→sanitized webhook, recovery→resolve→webhook, slow/flaky→degraded,
   status-page leak check, ack/note/resolve, metrics/reliability).
2. README polish + Dockerfile/compose check (`docker compose up` path).
3. REVIEW.md + transcript export into `transcripts/`.

## Changes to the plan, with reasons

- **Incident for degraded → no** (planned): only `down` opens incidents; degraded is
  visible on dashboard/timeline. Reason: paging on every slow patch is noise; matches
  PLAN.md assumption Q1.
- **State machine hardening** (new): after 3+ failures, a single success no longer
  flips straight to `degraded` — the service stays `down` until 2 consecutive
  successes. Reason: unit tests exposed that one lucky success right after an outage
  could mask a real outage; also matches industry practice (recovery requires
  confirmation). Recorded in DECISIONS.md.
- **Default port 8001** (new): Docker Desktop already binds `0.0.0.0:8000` for another
  project on this laptop. Recorded in DECISIONS.md.
- **python-multipart + pytest-asyncio added** to requirements (needed by forms / async
  tests). No scope change.
