"""Preview gate — pause after segment 1 for operator approval."""
from __future__ import annotations

import logging
import os

from core.runtime.abort import set_director_abort

log = logging.getLogger(__name__)


def _preview_gate(mp4_path, config: dict) -> None:
    """Preview gate (R13): pause after segment 1 for operator approval."""
    from agents.director_agent import UIState

    seg_path_str = str(mp4_path) if mp4_path else "segment not available"

    if UIState.is_ui_mode:
        UIState.add_log(f"[PREVIEW] Segment 1 ready: {seg_path_str}")
        UIState.active_question = (
            f"PREVIEW: Segment 1 is ready. Review it and decide:\n"
            f"  Path: {seg_path_str}\n"
            f"  Type 'approve' to continue, anything else to abort."
        )
        UIState.status = "paused"
        UIState.pause_event.clear()
        timeout = int(os.environ.get("DIRECTOR_TIMEOUT", "0")) or 600
        if not UIState.pause_event.wait(timeout=timeout):
            log.warning("[PREVIEW] Timeout — proceeding with production")
            UIState.status = "running"
            UIState.active_question = None
            return
        UIState.status = "running"
        UIState.active_question = None
        reply = (UIState.user_reply or "").strip().lower()
        UIState.user_reply = None
        if "approve" not in reply:
            log.info("[PREVIEW] Operator rejected — aborting pipeline")
            set_director_abort(True)
        else:
            log.info("[PREVIEW] Operator approved — continuing production")
        return

    sep = "=" * 60
    print(f"\n{sep}")
    print("  PREVIEW — Segment 1 Ready")
    print(sep)
    print(f"\n  Segment 1 video: {seg_path_str}")
    print("  Open the file, review the look and sound, then decide.\n")

    try:
        import sys as _sys
        if not _sys.stdin.isatty():
            log.info("[PREVIEW] Non-interactive stdin — auto-approving")
            return
        choice = input("  [ENTER] Approve & continue  |  [q] Abort: ").strip().lower()
        if choice == "q":
            log.info("[PREVIEW] Operator aborted after preview")
            set_director_abort(True)
        else:
            log.info("[PREVIEW] Operator approved — continuing production")
    except (EOFError, KeyboardInterrupt):
        log.info("[PREVIEW] No input — auto-approving")
    print(sep + "\n")
