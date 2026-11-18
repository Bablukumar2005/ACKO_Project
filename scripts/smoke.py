"""Quick smoke test of the data + runtime layer (no web server)."""
import asyncio

from pulse import db, store, seed
from pulse.checker import run_check
from pulse.incidents import IncidentEngine
from pulse.notifier import Notifier
from pulse.scheduler import Scheduler

import httpx


async def main():
    db.reset()
    db.connect(":memory:")
    seed.seed_if_empty()
    services = store.list_services()
    print("services:", [s["name"] for s in services])

    engine = IncidentEngine(Notifier(httpx.AsyncClient()))
    sched = Scheduler(engine)
    sched.client = httpx.AsyncClient()
    sched.init_states()
    print("initial states:", sched.states)

    # One manual tick against the real kit if it's running; otherwise expect failures.
    svc = services[0]
    state = await sched.tick(svc)
    print(f"tick {svc['name']}: state={state}")

    results = store.last_results(svc["id"], 5)
    print("last results:", results)

    # Simulate a full down cycle through the engine.
    svc = services[1]  # payments
    fake = {"ok": False, "status_code": 503, "latency_ms": 4.2,
            "error_class": "http_5xx", "error_detail": "internal db stuff"}
    store.record_check(svc["id"], **fake)
    store.record_check(svc["id"], **fake)
    store.record_check(svc["id"], **fake)
    prev = sched.states[svc["id"]]
    await engine.on_transition(svc, prev, "down", type("R", (), fake)())
    incident = store.open_incident_for(svc["id"])
    print("incident opened:", incident["id"], incident["state"])
    print("events:", [(e["kind"], e["message"][:40]) for e in store.incident_events(incident["id"])])

    await asyncio.sleep(0.5)  # let background notification attempt finish
    from pulse.store import since_iso
    print("notification stats:", store.notification_stats(since_iso(1)))

    # Recovery path
    good = {"ok": True, "status_code": 200, "latency_ms": 3.0,
            "error_class": "none", "error_detail": ""}
    store.record_check(svc["id"], **good)
    store.record_check(svc["id"], **good)
    await engine.on_transition(svc, "down", "healthy", type("R", (), good)())
    resolved = store.get_incident(incident["id"])
    print("incident after recovery:", resolved["state"], "resolved_at:", resolved["resolved_at"])
    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
