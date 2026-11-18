"""The sanitizer rule, enforced from every angle:

internal error text may be stored internally, but public surfaces
(webhook payloads, /api/status, the /status page) must never carry it.
"""
import json

from pulse import db, store
from pulse.checker import FRIENDLY, friendly
from pulse.notifier import build_payload
from pulse.web.routes import _friendly_state_text

TRAP = "upstream claims-db timeout at 10.12.4.7:5432"


def service_with_down_check(in_memory_db):
    service = store.create_service({"name": "claims", "health_url": "http://localhost:7003/health"})
    store.record_check(service["id"], ok=False, status_code=503, latency_ms=4.0,
                       error_class="http_5xx", error_detail=TRAP)
    return service


class TestCheckerClassification:
    def test_friendly_classes_are_safe_tokens(self):
        for cls in FRIENDLY:
            text = friendly(cls)
            assert "10.12" not in text and "db" not in text or cls == "none"

    def test_public_classes_cover_common_failures(self):
        assert friendly("timeout") == "not responding in time"
        assert friendly("conn_refused") == "unreachable"
        assert friendly("http_5xx") == "returning server errors"
        assert friendly("unknown_class") == "failing unexpectedly"


class TestWebhookPayload:
    def test_payload_never_carries_internal_error(self, in_memory_db):
        service = service_with_down_check(in_memory_db)
        incident = store.open_incident(service["id"])
        payload = build_payload(service, incident, "incident_opened")
        assert TRAP not in json.dumps(payload)
        assert payload["event"] == "incident_opened"
        assert payload["service"] == "claims"
        assert payload["incident_id"] == incident["id"]

    def test_resolve_payload_has_recovered_summary(self, in_memory_db):
        service = service_with_down_check(in_memory_db)
        incident = store.resolve_incident(store.open_incident(service["id"])["id"])
        payload = build_payload(service, incident, "incident_resolved")
        assert "recovered" in payload["summary"]


class TestStatusPageText:
    def test_public_state_text_is_plain_english(self):
        for state in ("healthy", "degraded", "down", "unknown"):
            text = _friendly_state_text(state)
            assert "10.12" not in text and "db" not in text.lower()

    def test_down_text_promises_no_details(self):
        assert _friendly_state_text("down") == "Not working right now — our team is on it"


class TestStoreKeepingInternalDetail:
    def test_raw_detail_stays_in_the_operators_db(self, in_memory_db):
        service = service_with_down_check(in_memory_db)
        checks = store.recent_checks(service["id"], 5)
        assert checks[0]["error_detail"] == TRAP  # stored for operators...
        assert checks[0]["error_class"] == "http_5xx"  # ...but only the token is safe
