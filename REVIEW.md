# REVIEW.md — self-review

*(Written at the end of the build; kept to half a page as required.)*

## What works

Everything in the must-have list, wired end-to-end and verified three ways: 51 unit +
integration tests (no Docker needed), an 18-check acceptance script against the real
starter kit, and manual demo runs of the incident drill. The two behaviours I'm most
confident about are the graded heart of the product: **detection without noise**
(one slow/flaky service degrades; three consecutive failures open an incident; recovery
requires two confirmed successes) and **the sanitizer rule** (the kit's planted internal
error text can reach the operator DB but never `/status`, `/api/status`, or webhook
payloads — that exact assertion is automated). MTTD/MTTR and the error budget are
computed from stored raw checks, so the numbers are real, not fabricated.

## What doesn't work / what I'd do next

- **No auth on the operator view** — a deliberate cut; adding a session login is ~30 min
  and I'd do it first with more time.
- **`flaky` at high rates can open an incident** (3 consecutive failures) — I consider
  this correct-by-design (an incident IS happening), but a "flap suppression" rule
  (e.g. cooldown before re-opening) would reduce incident spam in long outages.
- **SQLite + one process** — fine for the demo scale (4 services × 17k checks/day),
  but I'd move to Postgres + a separate worker before calling it production-ready.
- **Availability is check-weighted** (fraction of failed checks), not second-weighted
  state time; at 5 s granularity the difference is small but explainable, and I say so
  on the service page.

## What the AI got wrong (and how I caught it)

1. **DB-lock deadlock** — `create_service()` called `get_service()` inside the same
   non-reentrant transaction lock; every seed hung forever. Diagnosed by checkpointed
   logging, fixed with an `RLock` + moving the read outside the write transaction.
2. **State machine flattery** — my first version let a single success right after an
   outage flip the service to `degraded` (masking a real outage). Unit tests for the
   recovery path caught it; the fix was a "recent full failure-run keeps the service
   down until recovery is confirmed" rule.
3. **A test that lied about flakiness** — my integration stub ignored the `?rate=`
   query and always used 0.5, so a "flaky never opens incidents" test failed randomly.
   The production code was right; the stub was wrong. Fixed the stub, then weakened the
   test's claim (at 50% flakiness, opening an incident is correct).
4. **Small stuff**: forgot `python-multipart` (forms crashed at import), pytest-asyncio
   config warning, a stray probe file committed (removed immediately).

## Own work vs AI work

The AI scaffolded fast, but every rule that matters — thresholds (3-down/2-recover),
incident-only-on-down, the sanitizer choke-point, error-budget math, what stays out of
scope — was decided by me and is defended in `DECISIONS.md`/`PLAN.md`. I verified each
AI-generated piece by running it; the four defects above were all found and fixed by
running tests, not by reading code and hoping.
