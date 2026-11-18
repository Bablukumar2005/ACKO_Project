"""Reliability: uptime windows vs target, and error-budget arithmetic.

Availability is check-weighted: fraction of successful checks in the window
(computed from stored attempts, so it's always real data).
"""
from . import db, store


def window_stats(service: dict, hours: float) -> dict:
    """Uptime %, budget usage and latency for one service over `hours`."""
    since = store.since_iso(hours)
    with db.tx() as c:
        row = c.execute(
            """SELECT COUNT(*) AS total, SUM(ok) AS ok,
                      AVG(latency_ms) AS avg_latency, MAX(latency_ms) AS max_latency
               FROM checks WHERE service_id=? AND ts>=?""",
            (service["id"], since),
        ).fetchone()
    total, ok = int(row["total"] or 0), int(row["ok"] or 0)
    target = float(service["target_pct"])
    window_seconds = hours * 3600
    failed = total - ok
    uptime_pct = round(100.0 * ok / total, 3) if total else None
    # Error budget: what the target allows, what was used, what remains.
    budget_seconds = round(window_seconds * (1 - target / 100), 1)
    used_seconds = round(window_seconds * (failed / total), 1) if total else 0.0
    remaining = round(budget_seconds - used_seconds, 1) if total else budget_seconds
    return {
        "window_label": _label(hours),
        "total": total,
        "ok": ok,
        "failed": failed,
        "uptime_pct": uptime_pct,
        "target_pct": target,
        "meets_target": None if uptime_pct is None else uptime_pct >= target,
        "budget_seconds": budget_seconds,
        "used_seconds": used_seconds,
        "remaining_seconds": remaining,
        "avg_latency_ms": round(row["avg_latency"], 1) if row["avg_latency"] is not None else None,
        "max_latency_ms": round(row["max_latency"], 1) if row["max_latency"] is not None else None,
    }


def reliability_card(service: dict) -> dict:
    """All standard windows for one service."""
    return [window_stats(service, h) for h in (1, 24, 24 * 7)]


def _label(hours: float) -> str:
    return {1: "1 hour", 24: "24 hours", 24 * 7: "7 days"}.get(hours, f"{hours:g}h")
