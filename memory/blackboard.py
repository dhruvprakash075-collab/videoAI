"""blackboard.py - Shared workspace for the Director/Writer/pipeline decision record.

Provides atomic, thread-safe read/write of the DecisionRecord on disk.
All agents read from and write to this single source of truth instead of
passing values down a master-slave chain.

Design:
- Atomic writes: temp file + os.replace (same pattern as CheckpointManager/WorldState).
- In-process lock: threading.RLock (same pattern as PermanentMemoryLog).
- Optional cross-process lock: filelock if available, silent no-op otherwise.
- Never requires a model in VRAM to read or write.
"""

import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from config.config_schemas import DecisionRecord

log = logging.getLogger(__name__)

# Optional cross-process file lock (no hard dependency)
try:
    from filelock import FileLock as _FileLock

    _FILELOCK_AVAILABLE = True
except ImportError:
    _FileLock = None
    _FILELOCK_AVAILABLE = False


class Blackboard:
    """Atomic, lock-guarded shared workspace for a single pipeline run.

    Stores the DecisionRecord plus any other shared state as JSON on disk.
    """

    FILENAME = "blackboard.json"

    def __init__(self, root: Path, topic_slug: str = ""):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        # P2-9 fix: key the blackboard file by topic slug so concurrent or
        # sequential runs on different topics don't share state.
        if topic_slug:
            filename = f"blackboard_{topic_slug}.json"
        else:
            filename = self.FILENAME
        self._path = self._root / filename
        self._lock = threading.RLock()
        self._file_lock = _FileLock(str(self._path) + ".lock") if _FILELOCK_AVAILABLE else None

    # ── Low-level read/write ───────────────────────────────────────────────

    def read(self) -> dict[str, Any]:
        """Read the full blackboard dict. Returns {} if not yet written."""
        with self._lock:
            if not self._path.exists():
                return {}
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"[BLACKBOARD] Read failed ({e}) — returning empty")
                return {}

    def write(self, patch: dict[str, Any]) -> None:
        """Merge patch into the blackboard and persist atomically."""
        with self._lock:
            ctx = self._file_lock if self._file_lock else contextlib.nullcontext()
            with ctx:
                current = self.read()
                current.update(patch)
                self._atomic_write(current)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write data atomically via temp file + os.replace."""
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
            log.debug(f"[BLACKBOARD] Written: {self._path}")
        except Exception as e:
            log.exception(f"[BLACKBOARD] Atomic write failed: {e}")
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    # ── DecisionRecord helpers ─────────────────────────────────────────────

    def read_decision(self) -> Optional["DecisionRecord"]:
        """Read and deserialize the stored DecisionRecord, or None if absent."""
        from config.config_schemas import load_decision_record

        raw = self.read()
        dr_raw = raw.get("decision_record")
        if not dr_raw:
            return None
        try:
            return load_decision_record(dr_raw)
        except Exception as e:
            log.warning(f"[BLACKBOARD] Could not load DecisionRecord: {e}")
            return None

    def write_decision(self, rec: "DecisionRecord") -> None:
        """Serialize and persist the DecisionRecord to the blackboard."""
        self.write({"decision_record": rec.model_dump()})
        log.info(
            f"[BLACKBOARD] DecisionRecord persisted "
            f"(segments={rec.segment_count.value}, "
            f"words/seg={rec.words_per_segment.value}, "
            f"mode={rec.run_mode.value})"
        )

    def clear(self) -> None:
        """Remove the blackboard file (e.g. for one-time-use cleanup)."""
        with self._lock:
            try:
                self._path.unlink(missing_ok=True)
                log.debug(f"[BLACKBOARD] Cleared: {self._path}")
            except Exception as e:
                log.warning(f"[BLACKBOARD] Clear failed: {e}")


def get_blackboard(config: dict[str, Any], topic_slug: str = "") -> Blackboard:
    """Return a Blackboard rooted at the configured checkpoint directory.

    Args:
        config:      The loaded pipeline config dict.
        topic_slug:  A safe filesystem slug for the current topic/run (e.g.
                     from ``_safe_filename(topic)``).  When provided the
                     blackboard file is named ``blackboard_{topic_slug}.json``
                     so different topics never share state (P2-9 fix).
                     Defaults to ``""`` which falls back to the legacy
                     ``blackboard.json`` for backward compatibility.
    """
    ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
    return Blackboard(ck_dir, topic_slug=topic_slug)
