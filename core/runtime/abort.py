"""Global director-abort flag (moved out of segment_runner)."""
from __future__ import annotations

import threading

_director_abort = False
_director_abort_lock = threading.Lock()


def _director_aborted() -> bool:
    with _director_abort_lock:
        return _director_abort


def set_director_abort(val: bool = True) -> None:
    """Public API to flip the Director abort flag."""
    with _director_abort_lock:
        global _director_abort
        _director_abort = val


def get_director_abort() -> bool:
    """Read the Director abort flag (for orchestrator to reset between runs)."""
    with _director_abort_lock:
        return _director_abort
