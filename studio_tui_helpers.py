"""
studio_tui_helpers.py - Pure helper functions for the Studio TUI.

These are extracted from studio_tui.py so they can be unit-tested without
importing Textual (which needs a real terminal). studio_tui.py imports from
here, so there is a single source of truth.
"""

import re
import time


def format_elapsed(run_start_ts: float) -> str:
    """Format elapsed seconds as mm:ss or 'Hh MMm SSs'. '—' if not started."""
    if not run_start_ts:
        return "—"
    elapsed = max(0.0, time.time() - run_start_ts)
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    s = int(elapsed % 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m:02d}:{s:02d}"


def format_etc(run_start_ts: float, current: int, total: int) -> str:
    """Estimate time to completion. '—' when not computable, '~0s' when done."""
    if not run_start_ts or total <= 0 or current <= 0:
        return "—"
    if current >= total:
        return "~0s"
    elapsed = max(0.0, time.time() - run_start_ts)
    remaining = (elapsed / current) * (total - current)
    if remaining < 60:
        return f"~{int(remaining)}s"
    return f"~{int(remaining // 60)}m {int(remaining % 60):02d}s"


def parse_duration(value) -> "int | None":
    """Parse a duration (minutes). Returns None if invalid or out of 1..480."""
    try:
        v = int(str(value).strip())
        return v if 1 <= v <= 480 else None
    except (ValueError, TypeError):
        return None


def safe_filename(name: str) -> str:
    """Reduce a name to safe filename chars (alphanumerics, _ and -)."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def vram_high(vram_text: str, threshold: float = 80.0) -> bool:
    """True if VRAM usage text like '4.8/6.0GB (80%)' is at/above threshold."""
    try:
        if "%" in vram_text:
            pct = float(vram_text.rsplit("(", maxsplit=1)[-1].replace("%)", "").strip())
            return pct >= threshold
    except (ValueError, IndexError):
        pass
    return False
