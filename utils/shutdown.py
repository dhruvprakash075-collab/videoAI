"""shutdown.py — Graceful shutdown signal handlers.

Wires SIGINT (Ctrl-C) and SIGTERM to a cleanup sequence that:
  1. Sets a global "shutting down" flag so other code can stop retrying.
  2. Runs caller-registered cleanup hooks (Ollama evict, checkpoint save).
  3. Calls sys.exit(130) (the conventional 128+SIGINT code).

Usage in bootstrap_pipeline.py:
    from utils.shutdown import register_shutdown_handlers, register_cleanup_hook
    register_shutdown_handlers()
    register_cleanup_hook(lambda: evict_ollama_models(config, reason="shutdown"))

Usage in studio_tui.py: same pattern; the cleanup hook should also
    save UIState to a checkpoint file.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

_shutting_down = threading.Event()
_cleanup_hooks: list[Callable[[], None]] = []
_handlers_registered = False
_lock = threading.Lock()


def is_shutting_down() -> bool:
    """True if a shutdown signal has been received. Other code can poll this
    to break out of retry loops early instead of fighting a doomed run."""
    return _shutting_down.is_set()


def register_cleanup_hook(hook: Callable[[], None]) -> None:
    """Register a callable to run during shutdown. Order is FIFO.

    Each hook is wrapped in try/except so a bad hook doesn't break the chain.
    Hooks are called in the main thread (signal handlers run there on most
    platforms); long hooks should be quick. Heavy work belongs in a
    background thread spawned before the hook returns.
    """
    with _lock:
        _cleanup_hooks.append(hook)


def _run_cleanup_hooks() -> None:
    """Run all registered cleanup hooks. Each is best-effort."""
    with _lock:
        hooks = list(_cleanup_hooks)
    for hook in hooks:
        try:
            t0 = time.perf_counter()
            hook()
            log.info(
                "Shutdown hook %s ran in %.2fs",
                getattr(hook, "__name__", repr(hook)),
                time.perf_counter() - t0,
            )
        except Exception as e:
            log.warning(
                "Shutdown hook %s raised: %s",
                getattr(hook, "__name__", repr(hook)),
                e,
            )


def _handle_signal(signum: int, frame: object) -> None:
    """SIGINT/SIGTERM handler. Runs cleanup once, then exits with 128+signum."""
    try:
        name = signal.Signals(signum).name
    except (ValueError, AttributeError):
        name = str(signum)
    if _shutting_down.is_set():
        log.warning("Second %s signal received -- forcing exit", name)
        sys.exit(128 + int(signum))
    log.info("Received %s -- shutting down gracefully", name)
    _shutting_down.set()
    _run_cleanup_hooks()
    sys.exit(128 + int(signum))


def register_shutdown_handlers() -> bool:
    """Install signal handlers for SIGINT and SIGTERM. Returns True on success.

    Safe to call multiple times; subsequent calls are no-ops.
    On Windows, SIGTERM is not reliably delivered (only via taskkill /F), so
    it's still installed but won't fire in normal interactive use.
    """
    global _handlers_registered
    with _lock:
        if _handlers_registered:
            return True
        try:
            signal.signal(signal.SIGINT, _handle_signal)
        except (ValueError, OSError) as e:
            log.warning("Could not install SIGINT handler: %s", e)
        if hasattr(signal, "SIGTERM"):
            try:
                signal.signal(signal.SIGTERM, _handle_signal)
            except (ValueError, OSError) as e:
                log.warning("Could not install SIGTERM handler: %s", e)
        # SIGBREAK is a Windows-only signal from Ctrl+Break in the console.
        if hasattr(signal, "SIGBREAK"):
            try:
                signal.signal(signal.SIGBREAK, _handle_signal)  # type: ignore[attr-defined]
            except (ValueError, OSError) as e:
                log.warning("Could not install SIGBREAK handler: %s", e)
        _handlers_registered = True
    log.debug("Shutdown handlers registered")
    return True


def _reset_for_tests() -> None:
    """Internal: reset module state. Used by tests; not part of public API."""
    global _handlers_registered
    with _lock:
        _cleanup_hooks.clear()
        _shutting_down.clear()
        _handlers_registered = False
