"""Unit tests for reliability windows, error budgets and the sanitizer surface."""
from pulse import reliability, store, util


def make_service(target_pct: float) -> dict:
    return store.create_service({
        "name": f"svc-{target_pct}", "health_url": "http://localhost:7001/health",
        "target_pct": target_pct,
    })


def seed_checks(service_id: int, ok_count: int, fail_count: int) -> None:
    """Insert checks with fresh timestamps (inside every standard window)."""
    from pulse import db
    now = util.utcnow()
    with db.tx(write=True) as c:
        for i in range(ok_count):
            c.execute(
                "INSERT INTO checks (service_id, ts, ok, status_code, latency_ms, error_class, error_detail)"
                " VALUES (?,?,?,?,?,?,?)",
                (service_id, util.iso(now), 1, 200, 5.0, "none", ""))
        for i in range(fail_count):
            c.execute(
                "INSERT INTO checks (service_id, ts, ok, status_code, latency_ms, error_class, error_detail)"
                " VALUES (?,?,?,?,?,?,?)",
                (service_id, util.iso(now), 0, 503, 0.0, "http_5xx", "internal db thing"))


class TestReliability:
    def test_all_ok_is_100_and_within_budget(self, in_memory_db):
        service = make_service(99.9)
        seed_checks(service["id"], ok_count=10, fail_count=0)
        stats = reliability.window_stats(service, hours=24)
        assert stats["uptime_pct"] == 100.0
        assert stats["meets_target"] is True
        assert stats["budget_seconds"] == round(24 * 3600 * 0.001, 1)
        assert stats["used_seconds"] == 0.0
        assert stats["remaining_seconds"] == stats["budget_seconds"]

    def test_error_budget_math(self, in_memory_db):
        # 24h window at 99% target -> budget = 864s. 20% failures -> used 17280s.
        service = make_service(99.0)
        seed_checks(service["id"], ok_count=8, fail_count=2)
        stats = reliability.window_stats(service, hours=24)
        assert stats["budget_seconds"] == 864.0
        assert stats["used_seconds"] == round(24 * 3600 * 0.2, 1)
        assert stats["remaining_seconds"] == round(864 - 24 * 3600 * 0.2, 1)
        assert stats["meets_target"] is False  # 80% < 99%

    def test_empty_window_gives_none_not_crash(self, in_memory_db):
        service = make_service(99.9)
        stats = reliability.window_stats(service, hours=1)
        assert stats["uptime_pct"] is None
        assert stats["meets_target"] is None
        assert stats["total"] == 0

    def test_window_labels(self, in_memory_db):
        service = make_service(99.9)
        seed_checks(service["id"], ok_count=1, fail_count=0)
        assert reliability.window_stats(service, 1)["window_label"] == "1 hour"
        assert reliability.window_stats(service, 24)["window_label"] == "24 hours"
        assert reliability.window_stats(service, 24 * 7)["window_label"] == "7 days"

    def test_reliability_card_has_three_windows(self, in_memory_db):
        service = make_service(99.9)
        card = reliability.reliability_card(service)
        assert [w["window_label"] for w in card] == ["1 hour", "24 hours", "7 days"]
