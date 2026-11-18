"""Product metrics: is Pulse itself doing its job?

MTTD  — median seconds from the last good check to incident open (stored at open time).
MTTR  — median seconds from incident open to resolution.
Checker performance — success rate and latency percentiles of our own check runs.
Notification delivery — did the support team actually get told?
"""
import json
import statistics

from . import db, store
from .util import dur


def mttd_mttr(hours: float = 24) -> dict:
    since = store.since_iso(hours)
    with db.tx() as c:
        rows = c.execute(
            """SELECT detected_after_s, opened_at, resolved_at
               FROM incidents WHERE opened_at>=?""", (since,),
        ).fetchall()
    detected = [r["detected_after_s"] for r in rows if r["detected_after_s"] is not None]
    durations = []
    for r in rows:
        if r["resolved_at"]:
            from .util import parse
            durations.append((parse(r["resolved_at"]) - parse(r["opened_at"])).total_seconds())
    detected.sort()
    durations.sort()

    def median_or_none(values):
        return round(statistics.median(values), 1) if values else None

    mttd = median_or_none(detected)
    mttr = median_or_none(durations)
    return {
        "window_label": f"last {hours:g}h",
        "incidents": len(rows),
        "mttd_seconds": mttd,
        "mttd_human": dur(mttd),
        "mttr_seconds": mttr,
        "mttr_human": dur(mttr),
    }


def checker_performance(hours: float = 1) -> dict:
    since = store.since_iso(hours)
    with db.tx() as c:
        rows = c.execute(
            """SELECT latency_ms, ok FROM checks WHERE ts>=?""", (since,),
        ).fetchall()
    latencies = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
    ok = sum(1 for r in rows if r["ok"])
    n = len(rows)

    def pct(p):
        if not latencies:
            return None
        idx = min(len(latencies) - 1, int(round(p / 100 * (len(latencies) - 1))))
        return round(latencies[idx], 1)

    return {
        "window_label": f"last {hours:g}h",
        "checks": n,
        "success_rate_pct": round(100 * ok / n, 2) if n else None,
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
    }


def notification_summary(hours: float = 24) -> dict:
    stats = store.notification_stats(store.since_iso(hours))
    stats["window_label"] = f"last {hours:g}h"
    return stats


def incident_counts() -> dict:
    with db.tx() as c:
        rows = c.execute("SELECT state, COUNT(*) AS n FROM incidents GROUP BY state").fetchall()
    counts = {r["state"]: int(r["n"]) for r in rows}
    return {
        "open": counts.get("open", 0),
        "acknowledged": counts.get("acknowledged", 0),
        "resolved": counts.get("resolved", 0),
        "total": sum(counts.values()),
    }


def overview() -> dict:
    return {
        "incidents": incident_counts(),
        "detection_resolution": mttd_mttr(24),
        "checker": checker_performance(1),
        "notifications": notification_summary(24),
    }
