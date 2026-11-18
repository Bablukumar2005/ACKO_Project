"""All database access lives here (db.py is only schema/connections)."""
import json

from . import db
from .util import now_iso, parse, utcnow

# --------------------------------------------------------------------------- services


def create_service(data: dict) -> dict:
    with db.tx(write=True) as c:
        cur = c.execute(
            """INSERT INTO services
               (name, health_url, interval_s, timeout_s, degraded_latency_ms,
                failures_to_down, successes_to_recover, owner, target_pct,
                webhook_url, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["name"], data["health_url"], data.get("interval_s", 5.0),
                data.get("timeout_s", 4.0), data.get("degraded_latency_ms", 1000),
                data.get("failures_to_down", 3), data.get("successes_to_recover", 2),
                data.get("owner", ""), data.get("target_pct", 99.9),
                data.get("webhook_url", ""), int(data.get("enabled", True)), now_iso(),
            ),
        )
        return get_service(cur.lastrowid)


def get_service(service_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        return dict(row) if row else None


def get_service_by_name(name: str) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM services WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None


def list_services() -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT * FROM services ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def update_service(service_id: int, fields: dict) -> dict | None:
    if not fields:
        return get_service(service_id)
    allowed = {
        "health_url", "interval_s", "timeout_s", "degraded_latency_ms",
        "failures_to_down", "successes_to_recover", "owner", "target_pct",
        "webhook_url", "enabled",
    }
    cols, vals = [], []
    for key, value in fields.items():
        if key in allowed:
            cols.append(f"{key}=?")
            vals.append(int(value) if key == "enabled" else value)
    if not cols:
        return get_service(service_id)
    vals.append(service_id)
    with db.tx(write=True) as c:
        c.execute(f"UPDATE services SET {', '.join(cols)} WHERE id=?", vals)
    return get_service(service_id)


def delete_service(service_id: int) -> bool:
    with db.tx(write=True) as c:
        cur = c.execute("DELETE FROM services WHERE id=?", (service_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- checks


def record_check(service_id: int, *, ok: bool, status_code: int | None,
                 latency_ms: float, error_class: str, error_detail: str) -> None:
    with db.tx(write=True) as c:
        c.execute(
            """INSERT INTO checks (service_id, ts, ok, status_code, latency_ms,
                                   error_class, error_detail)
               VALUES (?,?,?,?,?,?,?)""",
            (service_id, now_iso(), int(ok), status_code, latency_ms,
             error_class, error_detail),
        )


def last_results(service_id: int, n: int) -> list[dict]:
    """The n most recent checks, oldest -> newest: [{'ok': bool, 'latency_ms': float}]."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT ok, latency_ms FROM checks WHERE service_id=? ORDER BY id DESC LIMIT ?",
            (service_id, n),
        ).fetchall()
    return [{"ok": bool(r["ok"]), "latency_ms": r["latency_ms"] or 0.0}
            for r in reversed(rows)]


def checks_since(service_id: int, since_iso: str) -> list[dict]:
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM checks WHERE service_id=? AND ts>=? ORDER BY id",
            (service_id, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_checks(service_id: int, limit: int = 20) -> list[dict]:
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM checks WHERE service_id=? ORDER BY id DESC LIMIT ?",
            (service_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def last_ok_before(service_id: int, ts_iso: str) -> str | None:
    with db.tx() as c:
        row = c.execute(
            "SELECT ts FROM checks WHERE service_id=? AND ok=1 AND ts<=? ORDER BY id DESC LIMIT 1",
            (service_id, ts_iso),
        ).fetchone()
        return row["ts"] if row else None


# --------------------------------------------------------------------------- incidents


def open_incident(service_id: int) -> dict:
    """Create an incident (assumes none open). Captures MTTD at open time."""
    opened = now_iso()
    last_ok = last_ok_before(service_id, opened)
    detected = (parse(opened) - parse(last_ok)).total_seconds() if last_ok else None
    with db.tx(write=True) as c:
        cur = c.execute(
            """INSERT INTO incidents (service_id, opened_at, state, detected_after_s)
               VALUES (?,?, 'open', ?)""",
            (service_id, opened, detected),
        )
        iid = cur.lastrowid
        c.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?,?,?,?)",
            (iid, opened, "opened", "Incident opened: service is down"),
        )
    return get_incident(iid)


def get_incident(incident_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return dict(row) if row else None


def open_incident_for(service_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute(
            "SELECT * FROM incidents WHERE service_id=? AND resolved_at IS NULL",
            (service_id,),
        ).fetchone()
        return dict(row) if row else None


def add_event(incident_id: int, kind: str, message: str, ts: str | None = None) -> None:
    with db.tx(write=True) as c:
        c.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?,?,?,?)",
            (incident_id, ts or now_iso(), kind, message),
        )


def ack_incident(incident_id: int) -> dict | None:
    ts = now_iso()
    with db.tx(write=True) as c:
        row = c.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None or row["resolved_at"] is not None:
            return None
        c.execute(
            "UPDATE incidents SET state='acknowledged', acked_at=? WHERE id=?",
            (ts, incident_id),
        )
        c.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?,?,?,?)",
            (incident_id, ts, "acked", "Acknowledged by operator"),
        )
    return get_incident(incident_id)


def add_note(incident_id: int, note: str) -> dict | None:
    add_event(incident_id, "note", note)
    return get_incident(incident_id)


def resolve_incident(incident_id: int) -> dict | None:
    ts = now_iso()
    with db.tx(write=True) as c:
        row = c.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None or row["resolved_at"] is not None:
            return None
        duration = (parse(ts) - parse(row["opened_at"])).total_seconds()
        c.execute(
            "UPDATE incidents SET state='resolved', resolved_at=? WHERE id=?",
            (ts, incident_id),
        )
        c.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?,?,?,?)",
            (incident_id, ts, "resolved", f"Service recovered; incident closed after {duration:.0f}s"),
        )
    return get_incident(incident_id)


def list_incidents(state: str | None = None, limit: int = 50) -> list[dict]:
    query = "SELECT * FROM incidents"
    params: list = []
    if state:
        query += " WHERE state=?"
        params.append(state)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db.tx() as c:
        rows = c.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def incidents_for_service(service_id: int, limit: int = 20) -> list[dict]:
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM incidents WHERE service_id=? ORDER BY id DESC LIMIT ?",
            (service_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def incident_events(incident_id: int) -> list[dict]:
    with db.tx() as c:
        rows = c.execute(
            "SELECT * FROM incident_events WHERE incident_id=? ORDER BY id",
            (incident_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def open_incident_count(service_id: int) -> int:
    with db.tx() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM incidents WHERE service_id=? AND resolved_at IS NULL",
            (service_id,),
        ).fetchone()
        return int(row["n"])


# --------------------------------------------------------------------------- notifications


def record_notification(incident_id: int | None, event: str, target_url: str,
                        payload: dict, response_code: int | None, ok: bool,
                        attempts: int) -> None:
    with db.tx(write=True) as c:
        c.execute(
            """INSERT INTO notifications
               (incident_id, event, target_url, payload, response_code, ok, attempts, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (incident_id, event, target_url, json.dumps(payload, indent=2),
             response_code, int(ok), attempts, now_iso()),
        )


def notification_stats(since_iso: str) -> dict:
    with db.tx() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n, SUM(ok) AS ok FROM notifications WHERE ts>=?",
            (since_iso,),
        ).fetchone()
        total, ok = int(row["n"] or 0), int(row["ok"] or 0)
    return {"total": total, "delivered": ok,
            "delivery_rate_pct": round(100 * ok / total, 1) if total else None}


# --------------------------------------------------------------------------- time filter


def since_iso(hours: float) -> str:
    """UTC ISO timestamp `hours` ago — used as a window cutoff in queries."""
    from datetime import timedelta
    return (utcnow() - timedelta(hours=hours)).isoformat(timespec="seconds")
