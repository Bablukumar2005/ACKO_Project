"""API tests via Starlette's TestClient (scheduler stubbed; no live checking)."""
import json

from fastapi.testclient import TestClient
import pytest

from pulse import store


@pytest.fixture()
def client(in_memory_db, monkeypatch):
    """App without lifespan (no scheduler running); state defaults to 'unknown'."""
    from pulse import main

    app = main.create_app()
    monkeypatch.setattr(main, "seed_enabled", lambda: False)
    return TestClient(app)


def make_service():
    return store.create_service({"name": "quote-svc", "health_url": "http://localhost:7001/health"})


def test_create_and_list_services(client):
    response = client.post("/api/services", json={
        "name": "payments", "health_url": "http://localhost:7002/health",
        "owner": "team-pay", "target_pct": 99.5,
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "payments" and body["state"] == "unknown"

    listing = client.get("/api/services").json()
    assert [s["name"] for s in listing] == ["payments"]


def test_duplicate_name_conflicts(client):
    make_service()
    response = client.post("/api/services", json={
        "name": "quote-svc", "health_url": "http://localhost:7001/health"})
    assert response.status_code == 409


def test_validation_rejects_bad_target(client):
    response = client.post("/api/services", json={
        "name": "x", "health_url": "http://localhost:9/health", "target_pct": 120})
    assert response.status_code == 422


def test_service_detail_has_reliability_and_404(client):
    service = make_service()
    detail = client.get(f"/api/services/{service['id']}").json()
    assert len(detail["reliability"]) == 3
    assert client.get("/api/services/9999").status_code == 404


def test_patch_updates_and_disables(client):
    service = make_service()
    patched = client.patch(f"/api/services/{service['id']}",
                           json={"target_pct": 99.99, "enabled": False}).json()
    assert patched["target_pct"] == 99.99
    assert patched["enabled"] is False


def test_delete(client):
    service = make_service()
    assert client.delete(f"/api/services/{service['id']}").status_code == 204
    assert client.get(f"/api/services/{service['id']}").status_code == 404


def test_public_status_is_sanitized(client, in_memory_db):
    service = make_service()
    store.record_check(service["id"], ok=False, status_code=503, latency_ms=1.0,
                       error_class="http_5xx",
                       error_detail="upstream q-db timeout at 10.12.4.7:5432")
    body = client.get("/api/status").json()
    assert TRAP not in json.dumps(body) if (TRAP := "10.12.4.7") else True
    assert body["services"][0]["state"] in ("unknown", "degraded", "down")


def test_incident_flow_through_api(client, in_memory_db):
    service = make_service()
    incident = store.open_incident(service["id"])
    store.add_event(incident["id"], "note", "looking into it")

    listed = client.get("/api/incidents").json()
    assert listed[0]["service_name"] == "quote-svc"

    detail = client.get(f"/api/incidents/{incident['id']}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert "opened" in kinds and "note" in kinds

    acked = client.post(f"/api/incidents/{incident['id']}/ack").json()
    assert acked["state"] == "acknowledged"

    resolved = client.post(f"/api/incidents/{incident['id']}/resolve").json()
    assert resolved["state"] == "resolved"
    # A resolved incident cannot be acked again.
    assert client.post(f"/api/incidents/{incident['id']}/ack").status_code == 400


def test_metrics_endpoint_shape(client, in_memory_db):
    make_service()
    body = client.get("/api/metrics").json()
    assert set(body) == {"incidents", "detection_resolution", "checker", "notifications"}


def test_notes_validation(client, in_memory_db):
    service = make_service()
    incident = store.open_incident(service["id"])
    assert client.post(f"/api/incidents/{incident['id']}/notes",
                       json={"note": ""}).status_code == 422


# ------------------------------------------------------------------ navigation

NAV_LINKS = ["/", "/status", "/metrics"]
NAV_LABELS = ["Dashboard", "Public status", "Metrics"]


def _page(client, path: str) -> str:
    return client.get(path).text


def test_every_page_has_working_nav_links(client):
    for page in ("/", "/status", "/metrics"):
        html = _page(client, page)
        for href, label in zip(NAV_LINKS, NAV_LABELS):
            assert f'href="{href}"' in html, f"{page} missing nav link {href}"
            assert label in html, f"{page} missing nav label {label}"


def test_no_page_emits_a_zero_second_refresh(client):
    # content="0" is an instant infinite reload loop: it destroys the DOM under
    # the user's cursor and makes header navigation appear dead in a browser.
    for page in ("/", "/status", "/metrics"):
        html = _page(client, page)
        assert 'content="0"' not in html, f"{page} still has a 0-second reload loop"


def test_refresh_only_where_promised(client, in_memory_db):
    assert 'content="5"' in _page(client, "/")        # dashboard copy promises 5 s
    assert 'content="10"' in _page(client, "/status")  # status copy promises 10 s
    assert "http-equiv=\"refresh\"" not in _page(client, "/metrics")  # static page: none
    service = make_service()  # detail pages must not self-refresh (forms live there)
    assert "http-equiv=\"refresh\"" not in _page(client, f"/services/{service['id']}")
