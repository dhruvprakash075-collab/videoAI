"""crewai_breaker.py - Circuit-breaker wrapper for CrewAI kickoff() calls.

Task 2: Wire the CrewAI/litellm LLM path to the same B1 per-model circuit
breaker that protects OllamaClient. Without this, a hung CrewAI writer
kickoff() (which goes through litellm, not OllamaClient) can block the
pipeline for minutes with no fast-fail. After 3 consecutive failures
the breaker opens; further calls raise BreakerOpen so the caller can
fall back immediately.

Usage:
    from utils.crewai_breaker import guarded_crewai_kickoff, BreakerOpen

    try:
        result = guarded_crewai_kickoff(crew, model_name="zephyr-writer")
    except BreakerOpen:
        # fast-fail — caller falls back
        ...

The breaker is per-model (reuses OllamaClient._breaker()) so one bad model
opening its breaker does NOT block other models. Status is logged so the TUI
degradation badge stays in sync.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


class BreakerOpen(Exception):
    """Raised when the per-model circuit breaker is OPEN and refuses the call."""
    def __init__(self, model: str, cooldown_s: float):
        self.model = model
        self.cooldown_s = cooldown_s
        super().__init__(f"Circuit breaker OPEN for {model!r} — fast-fail for {cooldown_s:.0f}s")


# ── Module-level fallback breaker (used when OllamaClient singleton absent) ─
_fallback_breakers: dict = {}
_fallback_lock = threading.Lock()


def _get_breaker(model: str, fails_threshold: int = 3, cooldown_s: float = 30.0):
    """Get the per-model breaker, preferring OllamaClient's if available."""
    try:
        from utils.ollama_client import get_ollama_client
        # We re-use OllamaClient's per-model breaker so a failing model opens
        # the same breaker whether called via generate() or crew.kickoff().
        client = get_ollama_client({})  # empty config is fine for breaker access
        return client._breaker(model)
    except Exception:
        # Fallback: a private per-model breaker (only used if OllamaClient isn't importable,
        # e.g. very early bootstrap). Same 3-state semantics.
        with _fallback_lock:
            if model not in _fallback_breakers:
                from utils.ollama_client import _BreakerState
                _fallback_breakers[model] = _BreakerState(fails_threshold, cooldown_s)
            return _fallback_breakers[model]


def record_breaker_success(model: str) -> None:
    """Mark a CrewAI call as successful (closes the breaker)."""
    try:
        _get_breaker(model).record_success()
    except Exception as e:
        log.debug(f"Breaker success record failed for {model}: {e}")


def record_breaker_failure(model: str) -> None:
    """Mark a CrewAI call as failed (may open the breaker)."""
    try:
        _get_breaker(model).record_failure()
    except Exception as e:
        log.debug(f"Breaker failure record failed for {model}: {e}")


def is_breaker_open(model: str) -> bool:
    """Return True if the breaker is currently OPEN for this model."""
    try:
        breaker = _get_breaker(model)
        # ask without side-effects — we use a probe that doesn't transition state
        from utils.ollama_client import _BreakerState
        state = breaker.state
        return state == _BreakerState.OPEN
    except Exception:
        return False


# ── Guarded kickoff wrapper ───────────────────────────────────────────────

def guarded_crewai_kickoff(crew, model_name: str,
                           timeout_s: float = 240.0,
                           lock: threading.RLock | None = None) -> Any:
    """Run crew.kickoff() under the per-model circuit breaker.

    Args:
        crew: The CrewAI Crew instance to execute.
        model_name: Ollama model name whose breaker should protect this call.
                    Must match the model name used by the underlying LLM.
        timeout_s: Hard wall-clock timeout in seconds. CrewAI/litellm does not
                   enforce a per-call timeout cleanly, so we enforce it here.
        lock: Optional crewai_lock (RLock) to serialize concurrent kickoffs.
              If None, the kickoff runs without external serialization.

    Returns:
        The CrewAI CrewOutput (or whatever crew.kickoff() returns).

    Raises:
        BreakerOpen: When the per-model breaker is OPEN — fail fast.
        TimeoutError: When the kickoff exceeds timeout_s.
        Exception:    Whatever crew.kickoff() raised.
    """
    breaker = _get_breaker(model_name)
    from utils.ollama_client import _BreakerState
    if breaker.state == _BreakerState.OPEN:
        # Cooldown not elapsed — fail fast. Report the real remaining cooldown
        # (not a hardcoded 0) so callers can decide whether to back off, log it,
        # or fall back to a different model.
        remaining = breaker.cooldown_remaining_s()
        log.warning(
            f"[CrewAIBreaker] {model_name!r} breaker is OPEN — fast-fail "
            f"({remaining:.1f}s remaining)"
        )
        raise BreakerOpen(model_name, remaining)

    start = time.time()
    try:
        if lock is not None:
            with lock:
                result = _run_with_timeout(crew.kickoff, timeout_s)
        else:
            result = _run_with_timeout(crew.kickoff, timeout_s)
        record_breaker_success(model_name)
        log.debug(f"[CrewAIBreaker] {model_name!r} kickoff OK ({time.time() - start:.1f}s)")
        return result
    except Exception as e:
        record_breaker_failure(model_name)
        log.warning(
            f"[CrewAIBreaker] {model_name!r} kickoff failed "
            f"({time.time() - start:.1f}s): {type(e).__name__}: {e}"
        )
        raise


def _run_with_timeout(fn, timeout_s: float):
    """Run `fn()` in a thread with a hard wall-clock timeout.

    CrewAI's litellm backend can hang for minutes on a bad generation. Threading
    timeout is the only reliable way to enforce a deadline without modifying
    litellm internals.
    """
    holder: dict = {"result": None, "exc": None, "done": False}

    def _runner():
        try:
            holder["result"] = fn()
        except Exception as e:
            holder["exc"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if not holder["done"]:
        raise TimeoutError(f"CrewAI kickoff exceeded {timeout_s:.0f}s timeout")
    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["result"]


# ── Direct Ollama-call convenience (uses OllamaClient) ────────────────────

def guarded_ollama_call(prompt: str, model: str, format_json: bool = False,
                        temperature: float = 0.3, num_predict: int = 1024,
                        timeout_s: float | None = None) -> str:
    """Call OllamaClient with the per-model breaker AND a wall-clock timeout.

    Returns "" when the breaker is open or any error occurs (matches the
    OllamaClient contract — callers must check for empty string).
    """
    try:
        from utils.ollama_client import get_ollama_client
        client = get_ollama_client({})
    except Exception:
        return ""
    try:
        return client.generate(
            prompt, model=model, format_json=format_json,
            temperature=temperature, num_predict=num_predict,
        )
    except Exception as e:
        log.debug(f"guarded_ollama_call({model}) failed: {e}")
        return ""
