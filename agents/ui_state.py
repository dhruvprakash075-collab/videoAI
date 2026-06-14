"""ui_state.py - Shared state for the local_ui.py web mode and TUI.

Split out of ``director_agent.py`` (2026-06-02) so the God module can shrink.
``UIState`` is a class-level (global) state bag that:

  * Is set externally by the operator UI (web dashboard, TUI, headless tests).
  * Is read by every pipeline component for status / pause / logs.
  * Uses ``_log_lock`` so concurrent appends (background pipeline thread +
    operator UI thread) don't tear the log list.

Backward compatibility
----------------------
``agents.director_agent`` re-exports ``UIState`` and ``_devanagari_ratio`` so
existing imports (``from agents.director_agent import UIState``) keep working
without touching the 30+ call sites across the codebase.

Adding a new field
------------------
If you add a NEW class attribute, you MUST also reset it in
``tests/conftest.py`` (the autouse ``reset_uistate`` fixture). Otherwise the
value will bleed between tests.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


# ── Devanagari quality helper (module-level, testable) ────────────────────


def _devanagari_ratio(text: str) -> float:
    """Return the fraction of alphabetic characters that are Devanagari (U+0900–U+097F).

    Returns 1.0 when there are no alphabetic characters (e.g. punctuation/numbers only)
    so we never trigger spurious re-translation on clean non-alpha output.
    Capped at 1.0 — Devanagari matras count as both alpha and Devanagari, so the
    raw ratio can exceed 1.0 for pure Devanagari text.
    """
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return 1.0
    deva = sum(1 for c in text if "\u0900" <= c <= "\u097f")
    return min(1.0, deva / total_alpha)


# ── UIState ──


class UIState:
    """Shared state for local_ui.py web mode. Set externally by the UI."""

    is_ui_mode = False

    pause_event = threading.Event()

    active_question = None

    user_reply = None

    status = "running"

    logs = []

    _log_maxlen = 1000

    _log_lock = threading.Lock()

    topic = ""

    character = "narrator"

    output_video = ""

    current_script = ""

    # A6: auto-accept flag — when True, consult_user/consult_fields return defaults without prompting
    auto_accept: bool = False

    # ── Progress / metrics (additive — safe defaults) ────────────────────────
    segment_current: int = 0  # segments completed so far
    segment_total: int = 0  # total planned segments (0 = unknown / planning)
    run_start_ts: float = 0.0  # time.time() when the run began (0 = not started)
    vram_text: str = ""  # human-readable VRAM line, or "" if unavailable

    # ── B2: Degradation ledger (additive — safe default) ─────────────────────
    degradations: list = []  # [{seg, stage, reason}] — silent fallbacks recorded here

    # ── Phase 0 manifest tracking ──
    run_id: str = ""
    vram_peaks: list = []
    warning_count: int = 0
    segment_manifests: dict = {}

    @classmethod
    def _uistate_log(cls, message: str) -> None:
        with cls._log_lock:
            if len(cls.logs) >= cls._log_maxlen:
                cls.logs = cls.logs[100:]
            cls.logs.append(message)
        log.info(message)

    @classmethod
    def add_log(cls, msg):
        with cls._log_lock:
            cls.logs.append(msg)
            if len(cls.logs) > cls._log_maxlen:
                cls.logs = cls.logs[-cls._log_maxlen :]

    @classmethod
    def add_degradation(cls, seg: int, stage: str, reason: str) -> None:
        """Record a silent quality fallback (B2). Thread-safe append."""
        with cls._log_lock:
            cls.degradations.append({"seg": seg, "stage": stage, "reason": reason})
            cls.warning_count += 1
        log.warning(f"[DEGRADATION] Seg {seg} | {stage}: {reason}")

    @classmethod
    def reset_run(cls, topic: str) -> None:
        """Initialize per-run metrics. Call before starting a new run.

        Zeroes segment counters so a 2nd run in the same session shows
        'planning' instead of stale values from the previous run.
        """
        import uuid
        cls.topic = topic
        cls.segment_current = 0
        cls.segment_total = 0
        cls.run_start_ts = time.time()
        cls.vram_text = ""
        cls.degradations = []  # B2: reset degradation ledger for new run
        cls.run_id = str(uuid.uuid4())
        cls.vram_peaks = []
        cls.warning_count = 0
        cls.segment_manifests = {}

    @classmethod
    def set_progress(cls, current: int | None = None, total: int | None = None) -> None:
        """Update segment progress. Either arg optional; ignores None.

        IMPORTANT: always call this with the value computed under the existing
        completed_segs_lock — never use UIState.segment_current + 1, which is
        a racy read-modify-write under parallel segment threads.
        """
        # NOTE: the int() casts are intentional runtime guards (tests pass floats
        # and expect coercion + a ValueError on non-castable input). Static type
        # checkers flag them as redundant because the params are typed int|None,
        # but callers in practice may pass floats/strings — keep the casts.
        if total is not None:
            cls.segment_total = int(total)  # type: ignore[redundant-cast]
        if current is not None:
            cls.segment_current = int(current)  # type: ignore[redundant-cast]
