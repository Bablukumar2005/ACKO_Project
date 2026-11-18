"""Unit tests for the pure state machine (no network, no Docker)."""
import pytest

from pulse.state import DOWN, DEGRADED, HEALTHY, UNKNOWN, compute_state


def ok(latency_ms=5.0):
    return {"ok": True, "latency_ms": latency_ms}


def fail():
    return {"ok": False, "latency_ms": 0.0}


def f2d(n):
    return dict(failures_to_down=n, successes_to_recover=2, degraded_latency_ms=1000)


class TestFreshData:
    def test_no_results_is_unknown(self):
        assert compute_state([], **f2d(3)) == UNKNOWN

    def test_one_success_is_unknown(self):
        assert compute_state([ok()], **f2d(3)) == UNKNOWN

    def test_two_successes_is_healthy(self):
        assert compute_state([ok(), ok()], **f2d(3)) == HEALTHY

    def test_one_failure_is_degraded(self):
        assert compute_state([fail()], **f2d(3)) == DEGRADED


class TestDown:
    def test_three_failures_is_down(self):
        assert compute_state([ok(), fail(), fail(), fail()], **f2d(3)) == DOWN

    def test_four_failures_is_down(self):
        assert compute_state([fail() for _ in range(4)], **f2d(3)) == DOWN

    def test_two_failures_is_only_degraded(self):
        assert compute_state([fail(), fail()], **f2d(3)) == DEGRADED

    def test_custom_threshold_one(self):
        assert compute_state([fail()], **f2d(1)) == DOWN

    def test_three_failures_with_higher_threshold_is_degraded(self):
        assert compute_state([fail(), fail(), fail()], **f2d(4)) == DEGRADED


class TestRecovery:
    def test_recovers_after_two_successes(self):
        history = [fail(), fail(), fail(), ok(), ok()]  # oldest -> newest
        assert compute_state(history, **f2d(3)) == HEALTHY

    def test_one_success_after_down_still_down(self):
        history = [fail(), fail(), fail(), ok()]
        assert compute_state(history, **f2d(3)) == DOWN

    def test_success_then_failure_restarts_recovery(self):
        history = [fail(), fail(), fail(), ok(), fail()]
        assert compute_state(history, **f2d(3)) == DOWN


class TestDegradedLatency:
    def test_fast_successes_are_healthy(self):
        assert compute_state([ok(5), ok(6)], **f2d(3)) == HEALTHY

    def test_slow_successes_are_degraded(self):
        # 3000 ms > 1000 ms threshold: the kit's `control/slow?ms=3000` mode.
        assert compute_state([ok(3000), ok(3000)], **f2d(3)) == DEGRADED

    def test_recovery_with_slow_successes_stays_degraded(self):
        history = [fail(), fail(), fail(), ok(3000), ok(3000)]
        assert compute_state(history, **f2d(3)) == DEGRADED

    def test_one_fast_one_slow_is_degraded(self):
        assert compute_state([ok(3000), ok(5)], **f2d(3)) == DEGRADED

    def test_long_slow_run_after_outage_is_degraded_not_down(self):
        # Recovered from an outage but still slow: degraded, not down.
        history = [fail(), fail(), fail()] + [ok(3000)] * 5
        assert compute_state(history, **f2d(3)) == DEGRADED


class TestFlaky:
    def test_flaky_mix_is_degraded_never_down(self):
        # 50% failures: never 3 consecutive fails, never 2 consecutive ok.
        history = [ok(), fail(), ok(), fail(), ok(), fail()]
        assert compute_state(history, **f2d(3)) == DEGRADED

    def test_flaky_ok_run_becomes_healthy(self):
        history = [ok(), fail(), ok(), ok()]
        assert compute_state(history, **f2d(3)) == HEALTHY

    def test_three_consecutive_flaky_failures_open_incident(self):
        history = [ok(), fail(), fail(), fail()]
        assert compute_state(history, **f2d(3)) == DOWN


class TestGuardrails:
    def test_never_returns_arbitrary_state(self):
        from pulse.state import ALL_STATES
        for history in ([], [ok()], [fail()], [ok(), fail(), fail(), ok()]):
            assert compute_state(history, **f2d(3)) in ALL_STATES
