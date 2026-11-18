# PROGRESS_STATUS.md — current project status

> The filename `PROGRESS.md` was already taken (existing Gate 2 deliverable preserved
> untouched), so this live status report lives here.

## Completed features

All implemented, tested, and verified against the running starter kit:

| Feature | Where | Evidence |
|---|---|---|
| Service catalogue (API + dashboard form), kit seeding | `store.py`, `seed.py` | 4 kit services registered; CRUD tests |
| Health checking, isolated per service | `checker.py`, `scheduler.py` | every check stored; timeout-class unit tests |
| State machine (unknown/healthy/degraded/down) | `state.py` (pure fn) | unit suite incl. flaky, slow, recovery-confirmation |
| Incidents: auto-open/auto-resolve, ack, notes, timeline, manual resolve | `incidents.py` | integration + acceptance lifecycle tests |
| Sanitized notifications: open + resolve webhooks, 3 attempts, logged | `notifier.py` | delivery rate in metrics; leak-check assertions |
| Reliability: 1h/24h/7d uptime % vs target + error budget | `reliability.py` | unit-tested math; live `/api/services/{id}` |
| Metrics: MTTD, MTTR, checker success + p50/p95/p99, delivery rate | `metrics.py` | `/api/metrics` verified live |
| Operator dashboard, service/incident pages, metrics page | `web/routes.py` + 7 templates | navigation regression tests |
| Sanitized public status page + `/api/status` | `/status` | acceptance test: kit's internal IP can never appear |
| JSON API: catalogue CRUD, status, incidents, metrics | `api/routes.py` | API contract unit tests |
| **Operator login**: users table in the existing `pulse.db`, bcrypt hashes, session cookies, admin-bootstrap, route guards (401/403/303), public reads stay open | `users.py`, `auth.py`, `db.py`, routes | 21 new unit tests; 22-check live script (`scripts/verify_auth.py`) all pass |
| Docker packaging (kit + optional Pulse container) | `Dockerfile`, `docker-compose.yml` | compose up verified |

## In-progress work

- **Nothing in code.** All approved features are complete and running.
- The **login feature and docs are uncommitted** in the working tree, pending the
  owner's commit approval (as requested, nothing has been committed).
- `PLAN.md` in the working tree is the restored Gate 1 original (per correction
  request); the consolidated plan moved to `PROJECT_PLAN.md` / `PROJECT_PLAN_V2.md`.

## Pending tasks

1. Commit the auth feature + documentation once approved.
2. Re-export the OpenCode session transcript into `transcripts/` (the current
   export predates the login work).
3. Re-run the live acceptance suite (`python -m pytest tests -q -m acceptance`)
   once the starter kit containers are confirmed healthy again.
4. Optional: run the kit's full `incident-drill.sh --fast` as a final demo.

## Current project status

- **Working and running**: Pulse serves `http://localhost:8001` with auth live
  (bootstrap admin `admin@pulse.local`, password from `PULSE_ADMIN_PASSWORD`).
- **Tests**: `python -m pytest tests -q` → **75 passed, 1 skipped** (the skip is the
  acceptance module auto-skipping while Pulse/kit aren't both reachable at test
  time); with the kit up, `… -m acceptance` → 9/9 passed (last full run).
- **Database**: the original `pulse.db` kept all data through the auth change
  (services 4, incidents 11, incident_events 49, notifications 18 rows unchanged);
  `users` table added with exactly 1 bootstrap admin row.
- Starter kit containers were observed down at ~19:40 (Docker Desktop slowness on
  this laptop); Pulse correctly classified all four services `down` and logged the
  failed webhook attempts — monitoring behaved as designed.

## Known issues

- **Flapping** (documented, deliberately not fixed): a service cycling down/up
  faster than the reopen design would allow opens a new incident + webhook per
  cycle (~120 incidents/hour worst case). Quantified with the planned fix in
  `DECISIONS.md`; uptime math stays truthful throughout.
- **Webhook delivery depends on the receiver being up** — failures are retried 3×,
  then logged as failed and reflected in the delivery rate (by design, not hidden).
- **`PULSE_SECRET` unset** → sessions are per-process random and reset on restart;
  a warning is logged each start. Set the env var to persist logins.
- **Port 8000 is unusable on this laptop** (Docker Desktop proxy owns it) — Pulse
  defaults to **8001**; 8080 is also taken by another project.

## Next steps

1. Owner approves → commit auth + docs (single commit or split: feature / docs).
2. Restart the kit containers; confirm `/status` returns to all-healthy.
3. Re-export transcripts; final review of `REVIEW.md` claims vs reality.
4. Future work queue (in priority order): flap suppression → roles UI →
   non-HTTP checks (see `PROJECT_PLAN_V2.md`).
