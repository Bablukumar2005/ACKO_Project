"""Acceptance tests: Pulse against the REAL starter kit.

Run with the kit up (docker compose up in the kit folder) and Pulse running:

    python run.py                       # Pulse on :8001
    python -m pytest tests -q -m acceptance

Skipped automatically when the kit or Pulse isn't reachable, so the normal
`pytest tests` run never fails on a laptop without the kit.
"""
import json
import time

import httpx
import pytest

PULSE = "http://127.0.0.1:8001"
KIT_PORTS = {"quotes": 7001, "payments": 7002, "claims": 7003, "notifications": 7004}
WEBHOOK = "http://127.0.0.1:9900"
INTERNAL_IP = "10.12.4.7"  # the kit's planted internal error text


def _reachable(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code < 500
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.acceptance

pytest.skip("starter kit / Pulse not running", allow_module_level=True) if not (
    _reachable(f"http://127.0.0.1:{KIT_PORTS['claims']}/health") and _reachable(PULSE)
) else None


def wait_for(fn, timeout=40, step=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(step)
    return None


def control(port: int, path: str) -> None:
    httpx.post(f"http://127.0.0.1:{port}{path}", timeout=5)


def services() -> list[dict]:
    return httpx.get(f"{PULSE}/api/services", timeout=5).json()


def by_name(name: str) -> dict:
    return next(s for s in services() if s["name"] == name)


def open_incident_for(name: str) -> dict | None:
    incidents = httpx.get(f"{PULSE}/api/incidents?state=open", timeout=5).json()
    return next((i for i in incidents if i["service_name"] == name), None)


def webhook_events() -> list:
    return httpx.get(f"{WEBHOOK}/api/received", timeout=5).json()


def test_01_kit_and_pulse_are_up():
    for name, port in KIT_PORTS.items():
        assert httpx.get(f"http://127.0.0.1:{port}/health", timeout=5).status_code == 200
    assert httpx.get(PULSE, timeout=5).status_code == 200


def test_02_catalogue_seeded_with_kit_services():
    names = {s["name"] for s in services()}
    assert set(KIT_PORTS) <= names


def test_03_all_services_healthy_baseline():
    def all_healthy():
        states = {s["name"]: s["state"] for s in services()}
        return states if all(v == "healthy" for v in states.values()) else None
    states = wait_for(all_healthy, timeout=30)
    assert states is not None, f"services not all healthy: {states}"


def test_04_down_opens_incident_and_notifies_sanitized():
    httpx.delete(f"{WEBHOOK}/api/received", timeout=5)
    control(KIT_PORTS["claims"], "/control/down")
    try:
        incident = wait_for(lambda: open_incident_for("claims"), timeout=40)
        assert incident is not None, "incident never opened for claims"

        payload = wait_for(
            lambda: next((p for p in webhook_events()
                          if p["body"].get("event") == "incident_opened"
                          and p["body"].get("service") == "claims"), None),
            timeout=15)
        assert payload is not None, "webhook never received incident_opened"
        body = json.dumps(payload["body"])
        assert INTERNAL_IP not in body, "internal error text leaked into webhook!"
        assert "claims is down" in payload["body"]["summary"]

        # Operator surfaces may show it, public must not.
        detail = httpx.get(f"{PULSE}/api/incidents/{incident['id']}", timeout=5).json()
        assert any(e["kind"] == "opened" for e in detail["events"])
    finally:
        control(KIT_PORTS["claims"], "/control/reset")


def test_05_public_status_page_never_leaks_internal_errors():
    control(KIT_PORTS["claims"], "/control/down")
    try:
        wait_for(lambda: open_incident_for("claims"), timeout=40)
        page = httpx.get(f"{PULSE}/status", timeout=5).text
        assert INTERNAL_IP not in page, "STATUS PAGE LEAKS INTERNAL ERRORS"
        assert "Not working right now" in page
        api = httpx.get(f"{PULSE}/api/status", timeout=5).json()
        assert INTERNAL_IP not in json.dumps(api)
    finally:
        control(KIT_PORTS["claims"], "/control/reset")


def test_06_recovery_resolves_incident_and_notifies():
    control(KIT_PORTS["claims"], "/control/down")
    incident = wait_for(lambda: open_incident_for("claims"), timeout=40)
    assert incident is not None
    control(KIT_PORTS["claims"], "/control/reset")

    def resolved():
        items = httpx.get(f"{PULSE}/api/incidents?state=resolved", timeout=5).json()
        return next((i for i in items if i["id"] == incident["id"]), None)
    resolved_incident = wait_for(resolved, timeout=40)
    assert resolved_incident is not None, "incident did not auto-resolve"

    notified = wait_for(
        lambda: next((p for p in webhook_events()
                      if p["body"].get("event") == "incident_resolved"
                      and p["body"].get("service") == "claims"), None),
        timeout=15)
    assert notified is not None, "webhook never received incident_resolved"


def test_07_slow_and_flaky_modes_stay_degraded_no_incident():
    control(KIT_PORTS["payments"], "/control/slow?ms=3000")
    try:
        states = wait_for(
            lambda: None if by_name("payments")["state"] != "degraded"
            else by_name("payments")["state"], timeout=30)
        assert states == "degraded"
        assert open_incident_for("payments") is None
    finally:
        control(KIT_PORTS["payments"], "/control/reset")

    control(KIT_PORTS["quotes"], "/control/flaky?rate=0.3")
    try:
        time.sleep(12)  # a few intervals
        assert open_incident_for("quotes") is None
    finally:
        control(KIT_PORTS["quotes"], "/control/reset")


def test_08_ack_note_and_manual_resolve_via_api():
    control(KIT_PORTS["notifications"], "/control/down")
    incident = wait_for(lambda: open_incident_for("notifications"), timeout=40)
    assert incident is not None
    iid = incident["id"]
    acked = httpx.post(f"{PULSE}/api/incidents/{iid}/ack", timeout=5).json()
    assert acked["state"] == "acknowledged"
    noted = httpx.post(f"{PULSE}/api/incidents/{iid}/notes",
                       json={"note": "acceptance note"}, timeout=5).json()
    assert noted["state"] == "acknowledged"
    detail = httpx.get(f"{PULSE}/api/incidents/{iid}", timeout=5).json()
    kinds = [e["kind"] for e in detail["events"]]
    assert "acked" in kinds and "note" in kinds
    control(KIT_PORTS["notifications"], "/control/reset")
    wait_for(lambda: httpx.get(f"{PULSE}/api/incidents/{iid}", timeout=5).json()["state"]
             == "resolved", timeout=40)


def test_09_metrics_and_reliability_reflect_the_drill():
    metrics = httpx.get(f"{PULSE}/api/metrics", timeout=5).json()
    assert metrics["incidents"]["total"] > 0
    assert "mttd_seconds" in metrics["detection_resolution"]
    assert "delivery_rate_pct" in metrics["notifications"]
    claims = httpx.get(f"{PULSE}/api/services/{by_name('claims')['id']}", timeout=5).json()
    windows = claims["reliability"]
    assert [w["window_label"] for w in windows] == ["1 hour", "24 hours", "7 days"]
    assert all("remaining_seconds" in w for w in windows)
