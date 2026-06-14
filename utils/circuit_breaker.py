"""circuit_breaker.py — Generic 3-state circuit breaker for all services.

Replaces the CrewAI-only breaker and OllamaClient's internal _BreakerState
with a single, importable CircuitBreaker that works for any service:
Ollama, ComfyUI, CrewAI, etc.

Usage:
    from utils.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry

    # Per-service breaker registry (auto-creates on first access)
    cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30)
    if cb.allow_request():
        try:
            result = call_service()
            cb.record_success()
        except Exception:
            cb.record_failure()
            raise
    else:
        raise BreakerOpen("comfyui", cb.cooldown_remaining_s())
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


class BreakerOpen(Exception):
    """Raised when a circuit breaker is OPEN and refuses the call."""

    def __init__(self, name: str, cooldown_s: float):
        self.name = name
        self.model = name
        self.cooldown_s = cooldown_s
        super().__init__(f"Circuit breaker OPEN for {name!r} — fast-fail for {cooldown_s:.0f}s")


class CircuitBreaker:
    """Per-instance 3-state circuit breaker (thread-safe).

    States:
        CLOSED:    requests pass through normally
        OPEN:      fail fast for cooldown_s after fails_threshold consecutive failures
        HALF_OPEN: allow ONE probe after cooldown; success → CLOSED, failure → OPEN
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, fails_threshold: int = 3, cooldown_s: float = 30.0):
        self.name = name
        self._fails_thresh = fails_threshold
        self._cooldown_s = cooldown_s
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._fail_count = 0
        self._open_until = 0.0

    # ── Query ──────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        """Return True if the request should be attempted (thread-safe)."""
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.time() >= self._open_until:
                    self._state = self.HALF_OPEN
                    log.info("[CB:%s] → Half-Open (probe allowed)", self.name)
                    return True
                return False
            # HALF_OPEN: allow exactly one probe
            return True

    def cooldown_remaining_s(self) -> float:
        """Seconds until OPEN→HALF_OPEN transition. 0.0 if not OPEN."""
        with self._lock:
            if self._state != self.OPEN:
                return 0.0
            remaining = self._open_until - time.time()
            return max(0.0, remaining)

    def is_open(self) -> bool:
        return not self.allow_request()

    # ── Transitions ────────────────────────────────────────────────────────

    def record_success(self) -> None:
        with self._lock:
            self._fail_count = 0
            if self._state != self.CLOSED:
                log.info("[CB:%s] → Closed (probe succeeded)", self.name)
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._fail_count += 1
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._open_until = time.time() + self._cooldown_s
                log.warning(
                    "[CB:%s] → Open (probe failed, cooldown %.0fs)",
                    self.name,
                    self._cooldown_s,
                )
            elif self._fail_count >= self._fails_thresh:
                self._state = self.OPEN
                self._open_until = time.time() + self._cooldown_s
                log.warning(
                    "[CB:%s] → Open after %d failures (cooldown %.0fs)",
                    self.name,
                    self._fail_count,
                    self._cooldown_s,
                )

    def reset(self) -> None:
        """Force-close the breaker (useful after config change or manual override)."""
        with self._lock:
            self._state = self.CLOSED
            self._fail_count = 0
            self._open_until = 0.0
            log.info("[CB:%s] reset → Closed", self.name)


class CircuitBreakerRegistry:
    """Thread-safe registry of named CircuitBreaker instances.

    Usage:
        cb = CircuitBreakerRegistry.get("ollama:hermes-director")
        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30)
    """

    _breakers: dict[str, CircuitBreaker] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, name: str, fails: int = 3, cooldown: float = 30.0) -> CircuitBreaker:
        """Get or create a named breaker."""
        with cls._lock:
            if name not in cls._breakers:
                cls._breakers[name] = CircuitBreaker(name, fails, cooldown)
            return cls._breakers[name]

    @classmethod
    def reset_all(cls) -> None:
        """Reset every registered breaker (e.g. after reload)."""
        with cls._lock:
            for cb in cls._breakers.values():
                cb.reset()

    @classmethod
    def summary(cls) -> dict[str, str]:
        """Return a snapshot of all breaker names → state."""
        with cls._lock:
            return {name: cb.state for name, cb in cls._breakers.items()}
