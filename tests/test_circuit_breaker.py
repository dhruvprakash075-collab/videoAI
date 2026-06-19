"""Tests for utils/circuit_breaker.py — generalized 3-state circuit breaker."""

from utils.circuit_breaker import BreakerOpen, CircuitBreaker, CircuitBreakerRegistry


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test", fails_threshold=2, cooldown_s=60)
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True
        assert cb.cooldown_remaining_s() == 0.0

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker("test", fails_threshold=2, cooldown_s=60)
        assert cb.state == CircuitBreaker.CLOSED
        cb.record_failure()  # 1
        assert cb.state == CircuitBreaker.CLOSED
        cb.record_failure()  # 2 → OPEN
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow_request() is False

    def test_half_open_probe(self):
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        cb.record_failure()  # → OPEN
        assert cb.allow_request() is False
        cb._open_until = 0.0
        # After cooldown, allow_request transitions to HALF_OPEN and returns True
        assert cb.allow_request() is True
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        cb.record_failure()  # → OPEN
        cb._open_until = 0.0
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()  # → CLOSED
        assert cb.state == CircuitBreaker.CLOSED


    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        cb.record_failure()  # → OPEN
        # Force to HALF_OPEN
        cb._state = CircuitBreaker.HALF_OPEN
        cb.record_failure()  # → OPEN again
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow_request() is False

    def test_reset(self):
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        cb.record_failure()  # → OPEN
        assert cb.state == CircuitBreaker.OPEN
        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_cooldown_remaining(self):
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        assert cb.cooldown_remaining_s() == 0.0
        cb.record_failure()  # → OPEN
        remaining = cb.cooldown_remaining_s()
        assert 55 <= remaining <= 60  # approx

    def test_registry_get_or_create(self):
        cb1 = CircuitBreakerRegistry.get("svc1", fails=3, cooldown=30)
        cb2 = CircuitBreakerRegistry.get("svc1", fails=5, cooldown=10)
        assert cb1 is cb2  # same instance
        assert cb1._fails_thresh == 3  # first-creation params win

    def test_registry_summary(self):
        CircuitBreakerRegistry.get("svc_a")
        CircuitBreakerRegistry.get("svc_b")
        summary = CircuitBreakerRegistry.summary()
        assert "svc_a" in summary
        assert "svc_b" in summary

    def test_registry_reset_all(self):
        cb = CircuitBreakerRegistry.get("reset_me", fails=1, cooldown=60)
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        CircuitBreakerRegistry.reset_all()
        assert cb.state == CircuitBreaker.CLOSED

    def test_breaker_open_exception(self):
        exc = BreakerOpen("ollama", 30.0)
        assert exc.name == "ollama"
        assert exc.cooldown_s == 30.0
        assert "ollama" in str(exc)
        assert "30" in str(exc)

    def test_half_open_allows_exactly_one_probe_concurrent(self):
        import concurrent.futures
        cb = CircuitBreaker("test", fails_threshold=1, cooldown_s=60)
        cb.record_failure()  # → OPEN
        cb._open_until = 0.0  # cooldown elapsed

        # Concurrently call allow_request
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: cb.allow_request(), range(10)))

        # Exactly one thread should have gotten True
        assert results.count(True) == 1
        assert results.count(False) == 9
