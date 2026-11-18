"""Integration tests: the full pipeline against in-process stub servers.

A stub "mock service" can go down/slow/flaky/reset like the starter kit's,
and a stub webhook records deliveries. Real asyncio, real HTTP, real SQLite
(file in a temp dir), no Docker required.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from pulse import db, store
from pulse.checker import run_check
from pulse.incidents import IncidentEngine
from pulse.notifier import Notifier
from pulse.scheduler import Scheduler
from pulse.state import DEGRADED, DOWN, HEALTHY


# --------------------------------------------------------------------- stubs

class StubKit:
    """One server that mimics the kit: /health with modes, /control/* to set them."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "normal"
        self.ms = 0
        self.rate = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                try:
                    self._do_GET()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass  # client gave up (timeout) — that's the test's point

            def _do_GET(self):
                with outer.lock:
                    mode, ms, rate = outer.mode, outer.ms, outer.rate
                if self.path == "/health":
                    if mode == "down":
                        # Contains internal-looking text, like the real kit's trap.
                        return self._send(503, {"status": "error",
                                                "error": "upstream stub-db timeout at 10.99.9.9:5432"})
                    if mode == "slow":
                        import time as t
                        t.sleep(ms / 1000)
                    if mode == "flaky":
                        import random
                        if random.random() < rate:
                            return self._send(500, {"status": "error", "error": "flaky boom"})
                    return self._send(200, {"status": "ok"})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                try:
                    self._do_POST()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass

            def _do_POST(self):
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                with outer.lock:
                    if parsed.path == "/control/down":
                        outer.mode, outer.ms, outer.rate = "down", 0, 0.0
                    elif parsed.path == "/control/slow":
                        outer.mode = "slow"
                        outer.ms = int(float(query.get("ms", ["2500"])[0]))
                        outer.rate = 0.0
                    elif parsed.path == "/control/flaky":
                        outer.mode = "flaky"
                        outer.ms = 0
                        outer.rate = float(query.get("rate", ["0.5"])[0])
                    elif parsed.path == "/control/reset":
                        outer.mode, outer.ms, outer.rate = "normal", 0, 0.0
                    else:
                        return self._send(404, {"error": "not found"})
                self._send(200, {"ok": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def control(self, path: str) -> None:
        with httpx.Client() as client:
            client.post(f"http://127.0.0.1:{self.port}{path}")

    def stop(self):
        self.httpd.shutdown()


class StubWebhook:
    def __init__(self):
        self.received: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.received.append(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()


# --------------------------------------------------------------------- fixtures

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh DB + running stub kit + stub webhook."""
    db.reset()
    monkeypatch.setattr(db, "_conn", None)
    db.connect(str(tmp_path / "test.db"))
    kit, hook = StubKit(), StubWebhook()
    yield {"kit": kit, "hook": hook}
    kit.stop()
    hook.stop()
    db.reset()


@pytest.fixture()
def service(env):
    return store.create_service({
        "name": "stub-svc",
        "health_url": f"http://127.0.0.1:{env['kit'].port}/health",
        "interval_s": 1.0, "timeout_s": 2.0, "degraded_latency_ms": 1000,
        "failures_to_down": 3, "successes_to_recover": 2,
        "target_pct": 99.0, "webhook_url": f"http://127.0.0.1:{env['hook'].port}/hook",
    })


@pytest.fixture()
async def pipeline(env, service):
    """Scheduler + engine wired to the stubs, driven manually via tick()."""
    engine = IncidentEngine(Notifier(httpx.AsyncClient()))
    sched = Scheduler(engine)
    sched.client = httpx.AsyncClient()
    sched.init_states()
    yield {"engine": engine, "sched": sched, "kit": env["kit"], "hook": env["hook"]}
    await sched.client.aclose()
    await engine.notifier.client.aclose()


async def ticks(pipeline, service, n):
    for _ in range(n):
        await pipeline["sched"].tick(service)
        await asyncio.sleep(0)


# --------------------------------------------------------------------- tests

async def test_healthy_cycle(pipeline, service):
    await ticks(pipeline, service, 3)
    assert pipeline["sched"].current_state(service["id"]) == HEALTHY


async def test_slow_service_is_degraded(pipeline, service):
    # Slow mode (2.5s) exceeds the 2s timeout: the check fails with a timeout,
    # which is how the real kit's slow mode behaves for a strict timeout.
    pipeline["kit"].control("/control/slow?ms=2500")
    await ticks(pipeline, service, 2)
    assert pipeline["sched"].current_state(service["id"]) == DEGRADED


async def test_down_opens_incident_and_notifies(pipeline, service, env):
    pipeline["kit"].control("/control/down")
    await ticks(pipeline, service, 3)
    assert pipeline["sched"].current_state(service["id"]) == DOWN
    incident = store.open_incident_for(service["id"])
    assert incident is not None
    # Wait for the background webhook delivery task to run.
    for _ in range(30):
        if env["hook"].received:
            break
        await asyncio.sleep(0.05)
    assert env["hook"].received, "webhook never received the opening notification"
    payload = env["hook"].received[0]
    assert payload["event"] == "incident_opened"
    assert payload["service"] == "stub-svc"
    assert "10.99.9.9" not in json.dumps(payload), "internal error leaked into webhook!"
    kinds = [e["kind"] for e in store.incident_events(incident["id"])]
    assert "opened" in kinds


async def test_recovery_resolves_and_notifies(pipeline, service, env):
    pipeline["kit"].control("/control/down")
    await ticks(pipeline, service, 3)
    incident = store.open_incident_for(service["id"])
    pipeline["kit"].control("/control/reset")
    await ticks(pipeline, service, 2)
    assert pipeline["sched"].current_state(service["id"]) == HEALTHY
    resolved = store.get_incident(incident["id"])
    assert resolved["state"] == "resolved"
    assert resolved["resolved_at"] is not None
    for _ in range(30):
        if any(p.get("event") == "incident_resolved" for p in env["hook"].received):
            break
        await asyncio.sleep(0.05)
    assert any(p.get("event") == "incident_resolved" for p in env["hook"].received)


async def test_flaky_rarely_down_and_no_incident_at_low_rate(pipeline, service):
    # At rate 0.15 over a few ticks, three consecutive failures are very unlikely
    # (p ~ 1%); the service should stay degraded/healthy, never open an incident.
    pipeline["kit"].control("/control/flaky?rate=0.15")
    for _ in range(6):
        await pipeline["sched"].tick(service)
        await asyncio.sleep(0)
        state = pipeline["sched"].current_state(service["id"])
        assert state != DOWN, "low-rate flakiness should not open incidents"
    assert store.open_incident_for(service["id"]) is None


async def test_ack_and_note_land_on_timeline(pipeline, service):
    from pulse.incidents import ack, add_note
    pipeline["kit"].control("/control/down")
    await ticks(pipeline, service, 3)
    incident = store.open_incident_for(service["id"])
    await ack(incident["id"])
    await add_note(incident["id"], "investigating the db pool")
    kinds = [e["kind"] for e in store.incident_events(incident["id"])]
    assert "acked" in kinds and "note" in kinds
    row = store.get_incident(incident["id"])
    assert row["state"] == "acknowledged"


async def test_every_check_is_stored(pipeline, service):
    await ticks(pipeline, service, 5)
    with db.tx() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM checks WHERE service_id=?",
                        (service["id"],)).fetchone()
    assert row["n"] == 5


async def test_checker_classifies_timeout(pipeline, service, env):
    env["kit"].control("/control/slow?ms=2500")  # slower than the 2s timeout
    result = await run_check(pipeline["sched"].client, service)
    assert result.ok is False
    assert result.error_class == "timeout"
    assert result.status_code is None
