"""
Video.AI Compatibility Layer

Provides Windows encoding fixes and import validation.
Previously contained langchain mock modules for Python 3.14 — no longer needed
since CrewAI 1.14+ is independent of langchain and we target Python 3.12.
"""

import logging
import sys
from importlib.util import find_spec

log = logging.getLogger(__name__)


def setup_compatibility():
    """Apply runtime compatibility fixes."""

    # P4-29 fix: removed stale langchain_core warning filter (CrewAI 1.14+ is
    # independent of langchain) and blanket pydantic DeprecationWarning suppression
    # (hides real issues).  Only suppress warnings that are genuinely unavoidable.

    # Fix Windows console encoding for Hindi/Devanagari output
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def check_dependencies():
    """Verify critical dependencies are importable. Returns list of missing packages."""
    missing = []

    try:
        import crewai
    except ImportError:
        missing.append("crewai")

    try:
        import ollama
    except ImportError:
        missing.append("ollama")

    # ponytail: dependency checks run during CLI/module startup; do not touch
    # CUDA here because torch.cuda.is_available() can hang on flaky drivers.
    if find_spec("torch") is None:
        missing.append("torch")

    try:
        import diffusers
    except ImportError:
        missing.append("diffusers")

    # `import peft` can transitively initialize heavy/optional Torch compile
    # paths on Windows. For startup validation we only need to know whether the
    # distribution is installed; functional PEFT failures should surface at the
    # actual call site.
    if find_spec("peft") is None:
        missing.append("peft")

    return missing


def apply_all_patches():
    """Apply all compatibility patches. Safe to call multiple times."""
    if getattr(sys, "_video_ai_compat_applied", False):
        return

    setup_compatibility()

    missing = check_dependencies()
    if missing:
        log.warning(
            f"Missing packages: {', '.join(missing)}. Install with: pip install {' '.join(missing)}"
        )

    sys._video_ai_compat_applied = True
    log.info("Compatibility layer initialized")


# Keep this module import-side-effect free. Entry points call apply_all_patches()
# explicitly after setting any process environment they need.
