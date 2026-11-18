"""SQLite access: schema, connections and a write lock.

One process-wide connection (WAL mode) guarded by a lock; helpers commit on
write. `reset()` exists for tests.
"""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_lock = threading.RLock()  # reentrant: store helpers may nest tx() blocks
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  health_url TEXT NOT NULL,
  interval_s REAL NOT NULL DEFAULT 5.0 CHECK (interval_s >= 1 AND interval_s <= 3600),
  timeout_s REAL NOT NULL DEFAULT 4.0 CHECK (timeout_s >= 0.5 AND timeout_s <= 30),
  degraded_latency_ms REAL NOT NULL DEFAULT 1000 CHECK (degraded_latency_ms >= 0),
  failures_to_down INTEGER NOT NULL DEFAULT 3 CHECK (failures_to_down BETWEEN 1 AND 10),
  successes_to_recover INTEGER NOT NULL DEFAULT 2 CHECK (successes_to_recover BETWEEN 1 AND 10),
  owner TEXT NOT NULL DEFAULT '',
  target_pct REAL NOT NULL DEFAULT 99.9 CHECK (target_pct >= 0 AND target_pct <= 100),
  webhook_url TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  ok INTEGER NOT NULL,
  status_code INTEGER,
  latency_ms REAL,
  error_class TEXT NOT NULL DEFAULT 'none',
  error_detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_checks_service_ts ON checks(service_id, ts);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  opened_at TEXT NOT NULL,
  resolved_at TEXT,
  acked_at TEXT,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','acknowledged','resolved')),
  detected_after_s REAL
);

CREATE TABLE IF NOT EXISTS incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id, id);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
  event TEXT NOT NULL,
  target_url TEXT NOT NULL,
  payload TEXT NOT NULL,
  response_code INTEGER,
  ok INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 1,
  ts TEXT NOT NULL
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (or reuse) the process-wide connection and ensure the schema."""
    global _conn
    if _conn is not None:
        return _conn
    path = path or __import__("pulse.config", fromlist=["config"]).DB_PATH
    if path != ":memory:":
        parent = Path(path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _conn.commit()
    return _conn


def conn() -> sqlite3.Connection:
    if _conn is None:
        return connect()
    return _conn


@contextmanager
def tx(write: bool = False):
    """Yield the connection inside the global lock; commit on write."""
    with _lock:
        c = conn()
        try:
            yield c
            if write:
                c.commit()
        except Exception:
            c.rollback()
            raise


def reset() -> None:
    """Close and forget the connection (used by tests)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
