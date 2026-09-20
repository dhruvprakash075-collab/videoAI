"""Regression: a late debounced stop must not kill post-production's Ollama.

Real failure this guards (session 2026-08-05): the pipeline schedules an Ollama
stop per segment; the timer sometimes fired AFTER post-production had
restarted the server for the SEO/upload LLM call, which then fell back to
static metadata. `start_ollama_server` now clears the armed stop.
"""

import threading
import time
from unittest.mock import patch

from core.runtime import ollama as rt


def _reset():
    rt.clear_ollama_stop_state()
    rt._EVICT_CACHE.clear()


def _start_with_fake_reachable(monkeypatch):
    """Run start_ollama_server against a patched reachable host."""
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: None)
    return rt.start_ollama_server({"ollama": {"host": "http://localhost:11434"}}, reason="test")


def test_start_clears_debounced_stop_before_it_fires(monkeypatch):
    """A stop armed right before post-production restarts must not fire."""
    _reset()
    stopped = []
    monkeypatch.setattr(rt, "stop_ollama_server", lambda config, reason="": stopped.append(reason))

    with patch("utils.url_security.open_validated_url") as _open:
        _open.return_value.__enter__ = lambda _s: _s
        _open.return_value.__exit__ = lambda *a: False
        # Arm the stop (segment finally-block), then immediately restart for
        # post-production's LLM call.
        rt.schedule_ollama_stop({}, delay=1.0)
        assert _start_with_fake_reachable(monkeypatch) is True

    time.sleep(1.4)
    assert stopped == [], f"debounced stop fired after restart: {stopped}"


def test_start_then_no_stop_pending_is_noop(monkeypatch):
    """Sanity: when nothing was armed, a start leaves nothing to cancel."""
    _reset()
    stopped = []
    monkeypatch.setattr(rt, "stop_ollama_server", lambda config, reason="": stopped.append(reason))
    with patch("utils.url_security.open_validated_url") as _open:
        _open.return_value.__enter__ = lambda _s: _s
        _open.return_value.__exit__ = lambda *a: False
        assert _start_with_fake_reachable(monkeypatch) is True
    time.sleep(0.2)
    assert stopped == []


def test_suppression_flag_blocks_the_armed_timer(monkeypatch):
    """Direct check of the flag the timer consults (no sleeps)."""
    _reset()
    called = []
    monkeypatch.setattr(rt, "stop_ollama_server", lambda config, reason="": called.append(reason))

    rt.schedule_ollama_stop({}, delay=5.0)
    # Simulate the restart path: start_ollama_server calls touch_ollama_active()
    # then _clear_ollama_stop_suppression() — here we only arm the flag while
    # the timer object still exists, which is the race the flag covers.
    rt.touch_ollama_active()
    rt._ollama_stop_suppressed = True
    rt._run_debounced_stop({})
    assert called == [], "suppressed timer still stopped the server"
    assert rt._ollama_stop_suppressed is False, "suppression flag not consumed"


def test_debounce_replaces_pending_timer(monkeypatch):
    """Last stop wins: an earlier armed stop is cancelled, not queued."""
    _reset()
    stopped = []
    monkeypatch.setattr(rt, "stop_ollama_server", lambda config, reason="": stopped.append(reason))
    rt.schedule_ollama_stop({}, delay=0.4)
    time.sleep(0.1)
    rt.schedule_ollama_stop({}, delay=0.4)  # debounce: first timer cancelled
    time.sleep(0.7)
    assert stopped == ["debounced-timer"], f"expected one stop, got {stopped}"


def test_thread_is_daemon_and_state_clears():
    """The timer must never keep the process alive; reset helper works."""
    _reset()
    rt.schedule_ollama_stop({}, delay=30.0)
    timer = rt._pending_ollama_timer
    assert timer is not None and isinstance(timer, threading.Timer)
    assert timer.daemon is True
    timer.cancel()
    rt.clear_ollama_stop_state()
    assert rt._pending_ollama_timer is None
