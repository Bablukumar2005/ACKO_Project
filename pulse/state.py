"""Service state machine — a PURE function over recent check results.

States: unknown | healthy | degraded | down.

Rules (thresholds come from the service catalogue):
  * >= failures_to_down consecutive failures            -> down
  * >= successes_to_recover consecutive successes, all
    within degraded_latency_ms                          -> healthy
    (same streak but too slow                           -> degraded)
  * a full failure-run in the recent window, recovery
    not yet confirmed with successes_to_recover wins    -> down
  * no data yet (and no failures)                       -> unknown
  * anything else (1-2 failures, slow successes,
    flaky mixes)                                        -> degraded

Pure = no hidden state, trivially unit-testable without Docker or network.
"""
from typing import Any

UNKNOWN = "unknown"
HEALTHY = "healthy"
DEGRADED = "degraded"
DOWN = "down"

ALL_STATES = (UNKNOWN, HEALTHY, DEGRADED, DOWN)


def _has_down_run(newest_first: list[dict[str, Any]], failures_to_down: int) -> bool:
    """True if the recent window contains failures_to_down consecutive failures."""
    run = 0
    for result in newest_first:
        if result["ok"]:
            run = 0
        else:
            run += 1
            if run >= failures_to_down:
                return True
    return False


def compute_state(
    results: list[dict[str, Any]],
    *,
    failures_to_down: int = 3,
    successes_to_recover: int = 2,
    degraded_latency_ms: float = 1000,
) -> str:
    """results: oldest -> newest, each {'ok': bool, 'latency_ms': float}."""
    if not results:
        return UNKNOWN
    newest_first = list(reversed(results))

    streak_fail = 0
    for result in newest_first:
        if result["ok"]:
            break
        streak_fail += 1
    if streak_fail >= failures_to_down:
        return DOWN

    streak_ok = 0
    for result in newest_first:
        if not result["ok"]:
            break
        streak_ok += 1
    if streak_ok >= successes_to_recover:
        recent_ok = newest_first[:streak_ok]
        within_latency = all(
            (r["latency_ms"] or 0.0) <= degraded_latency_ms for r in recent_ok
        )
        return HEALTHY if within_latency else DEGRADED

    # Recovery not yet confirmed: a recent full failure-run keeps the service down.
    if _has_down_run(newest_first, failures_to_down):
        return DOWN

    if streak_fail == 0 and len(results) < successes_to_recover:
        return UNKNOWN  # not enough evidence yet
    return DEGRADED


def is_worse(new: str, prev: str) -> bool:
    """down > degraded > healthy > unknown, for timeline 'worsened?' decisions."""
    order = {UNKNOWN: 0, HEALTHY: 1, DEGRADED: 2, DOWN: 3}
    return order[new] > order[prev]
