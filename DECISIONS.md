# DECISIONS.md — decision log

Each entry: time (IST on 2026-11-18), the decision, why, and alternatives considered.

## 18:25 — Choose Pulse (Option 1)

- **Decision:** build Pulse, the status and incident hub.
- **Why:** I understand the health-check → incident → status-page loop well; the kit's
  control endpoints (`down/slow/flaky`) map cleanly onto testable state-machine rules;
  it exercises schedulers, state machines and time-window math, which is the DevOps
  substance of the problem. Launchpad was attractive but is mostly Docker plumbing.
- **Alternative:** Launchpad — rejected within the 15-minute window.

## 18:26 — Single process + SQLite, not microservices/Postgres

- **Decision:** one Python app (FastAPI + asyncio) with SQLite (WAL mode).
- **Why:** in a 6-hour build, queues/containers/service-discovery are plumbing, not
  product. One process = one command to run, no serialization between components, easy
  to explain. SQLite is a real DB (survives restarts, does the window math in SQL) with
  zero setup.
- **Alternative:** Postgres container — rejected: setup cost for no benefit at this scale.

## 18:27 — asyncio (one task per service), not threads or Celery

- **Decision:** the scheduler spawns one `asyncio` task per enabled service; the
  checker awaits `httpx` with a per-request timeout.
- **Why:** the only real concurrency requirement is "one slow service must not hold up
  the others" (the kit tests this). Cooperative awaiting solves it with no GIL
  contention, no thread pool sizing, no broker.
- **Alternative:** threads — more failure modes; Celery+Redis — heavy.

## 18:28 — State machine as a pure function with configurable thresholds

- **Decision:** `compute_state(recent_results, failures_to_down=3, successes_to_recover=2,
  degraded_latency_ms) → unknown|healthy|degraded|down`. Rules: ≥3 consecutive failures
  → down; ≥2 consecutive in-budget successes → healthy; anything else (1–2 failures,
  latency above threshold, mixed flaky results) → degraded; no data → unknown.
- **Why:** pure = trivially unit-testable without Docker; thresholds live in the
  catalogue so each service can differ.
- **Assumption** (pending PO): only `down` opens an incident; degradation is visible
  but not page-worthy (see PLAN.md Q1).

## 18:29 — Store every check result, not just transitions

- **Decision:** every check attempt (ok or failed) is a row in `checks`.
- **Why:** reliability windows, error budgets and checker-performance metrics must be
  computed from real data (Healthchecks.io pattern), not reconstructed from state
  changes. Volume is trivial (4 services × ~17 k checks/day).

## 18:30 — Sanitizer choke-point for the public status page

- **Decision:** the checker classifies failures into safe tokens (`timeout`,
  `conn_refused`, `http_5xx`, …) and stores the raw message (`error_detail`) as
  internal-only. Public surfaces (`/status`, `/api/status`, webhook payloads) render
  **only** friendly text derived from the token.
- **Why:** the kit plants internal error text (`upstream claims-db timeout at
  10.12.4.7:5432`) in the `down` response; leaking it to customers is the trap. One
  choke-point beats filtering in every template.

## 18:31 — Webhook notifications: JSON, fire-and-forget delivery with 3 attempts

- **Decision:** on incident open and resolve, POST a sanitized JSON payload to the
  service's webhook (default: kit receiver `http://localhost:9900`). Delivery runs as a
  background task with 3 attempts + backoff; every attempt's outcome is logged in
  `notifications`, and the incident timeline records `notified` / `notify_failed`.
- **Why:** a slow/failing webhook must not stall the service's check loop (hence
  background), and "we notified support" must be provable (hence the log).

## 18:32 — No login for the operator view

- **Decision:** skip auth in this version.
- **Why:** single-operator internal tool for the demo; the graded must-haves don't
  include it. Recorded as a scope cut, revisit if time remains.

## 18:33 — Server-rendered Jinja2 UI, no JS framework

- **Decision:** Jinja2 templates + a small inline stylesheet + meta-refresh on the
  dashboard/status pages.
- **Why:** no npm/build toolchain in a 6-hour window; matches the kit's own UI style;
  the pages are read-mostly with small forms.

## 18:36 — Seeding

- **Decision:** on first run Pulse pre-registers the kit's four services
  (`quotes`, `payments`, `claims`, `notifications`) pointed at `localhost:7001–7004`,
  5 s interval, 99.9 % target; disable with `PULSE_SEED=0`.
- **Why:** makes the first `docker compose up` + `python run.py` demo work with zero
  manual setup; the catalogue API/UI can still edit everything.

## 18:41 — Default web port 8001, not 8000

- **Decision:** Pulse listens on 8001 by default (`PULSE_PORT` to override).
- **Why:** on this laptop Docker Desktop's proxy already owns `0.0.0.0:8000` for another
  project, which made a half-bound double-listener; 8080 was taken too. 8000 is a very
  common dev-machine clash in general.
- **Also noted:** Git Bash on this machine is WSL2 — it cannot reach Windows-side
  `127.0.0.1` ports, so the acceptance suite ships as pytest (runs on Windows Python)
  plus a POSIX `scripts/acceptance.sh` for macOS/Linux.

## 18:44 — State machine hardening: recovery must be confirmed

- **Decision:** after a full failure-run, one success no longer flips the service to
  `degraded`; it stays `down` until `successes_to_recover` consecutive successes.
- **Why:** unit tests exposed that a single lucky success right after an outage masked
  a real outage. A recovery should be confirmed, mirroring the "consecutive failures"
  rule on the way down.
- **Changed after Gate 1:** recorded here rather than editing PLAN.md, as instructed.

## 18:37 — Availability math (check-weighted)

- **Decision:** availability % in a window = successful checks ÷ total checks in that
  window; error budget = window × (1 − target); budget used = window × failure ratio;
  remaining = budget − used. Downtime seconds are also shown separately from incident
  records.
- **Why:** check-weighted is simple, explainable, and computed from stored attempts;
  per-second state modelling is overkill for a 5 s interval.

## 19:00 — Flapping: documented as a known limitation, not fixed today

- **Decision:** a service that repeatedly goes down and recovers (flapping) currently
  opens a NEW incident and fires a NEW webhook for every down/up cycle. We keep this
  behaviour and document it, rather than implementing flap suppression.
- **The failure mode, quantified:** with defaults (3 fails to down, 2 oks to recover,
  5 s interval) a full flap cycle is ≥ 25 s, so worst case ≈ 120 incidents and
  ~240 webhook deliveries per hour for one service. Two of the three consequences are
  harmful: support-team alert fatigue (defeats the product's purpose) and a
  misleadingly excellent MTTR (~25 s median) for a catastrophically unstable service.
  The third is correct: uptime % and the error budget truthfully crater.
- **What already limits the damage:** notifications fire only on open/resolve, so
  down→degraded→down wobbles create no duplicates or pings; every cycle still appends
  to a real timeline; reliability math stays honest.
- **The design we would implement (validated against PagerDuty/Nagios practice):**
  (1) reopen-window — if the service goes down again within `flap_window_s`
  (default 120 s, per service) of resolving, reopen the SAME incident
  (increment `reopen_count`, append "flapped down again (cycle #n)"), no new webhook;
  a down after the quiet window opens a fresh incident. (2) resolve summary reports
  "recovered after n flap cycles over Xm". (3) metrics: MTTR spans the whole unstable
  episode, plus a flap-stats row. (4) status page: "Having intermittent problems"
  wording when the open incident has reopen_count > 0, instead of strobing Up/Down.
  Estimated effort: ~1 hour including tests.
- **Why not now:** the must-have set is complete and verified (51 unit/integration +
  9 acceptance tests); this is an edge-condition refinement. Shipping it half-tested
  would risk the working core for a scenario the kit's drill does not exercise.
  We chose to spend the remaining time on demo preparation and documentation.
