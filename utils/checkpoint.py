"""checkpoint.py - Save/load pipeline state per step for resume support."""

import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from config import _safe_filename

log = logging.getLogger(__name__)


class CheckpointManager:
    _lock = threading.RLock()

    def __init__(
        self,
        checkpoint_dir: Path = Path("studio_checkpoints"),
        enabled: bool = True,
        max_age_hours: float = 0,
    ):
        self.dir = checkpoint_dir
        self.enabled = enabled
        self.max_age_hours = max_age_hours  # 0 = never expire
        if enabled:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, topic: str) -> Path:
        return self.dir / f"{_safe_filename(topic)}.json"

    def get(self, topic: str) -> dict | None:
        if not self.enabled:
            return None
        with CheckpointManager._lock:
            p = self._path(topic)
            if not p.exists():
                return None
            # P3-23: Never silently discard a checkpoint based on wall-clock age.
            # A paused/crashed run resumed the next day should not restart from
            # scratch.  Instead, emit a loud warning if the checkpoint is very
            # old (>48h) so the operator is aware, but still return it.
            # Expiry only happens via explicit clear() or a completion flag.
            age_h = (datetime.now().timestamp() - p.stat().st_mtime) / 3600
            if age_h > 48:
                log.warning(
                    f"[Checkpoint] '{topic}' checkpoint is {age_h:.1f}h old — "
                    "resuming anyway. Call checkpoint.clear() to start fresh."
                )
            elif self.max_age_hours > 0 and age_h > self.max_age_hours:
                # Soft warning for the configured threshold (e.g. 24h) — still return
                log.warning(
                    f"[Checkpoint] '{topic}' checkpoint is {age_h:.1f}h old "
                    f"(configured threshold: {self.max_age_hours}h) — resuming anyway."
                )
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning(f"Corrupt checkpoint {p} — ignoring")
                return None

    def _read_raw(self, topic: str) -> dict:
        """Bypass TTL — used by save() to avoid wiping steps on expiry."""
        with CheckpointManager._lock:
            p = self._path(topic)
            if not p.exists():
                return {}
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                bak = p.with_suffix(p.suffix + ".corrupt." + str(int(time.time())))
                try:
                    shutil.copy2(p, bak)
                    log.warning(f"Corrupt checkpoint backed up to {bak}")
                except Exception:
                    pass
                return {}

    class _CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, "__fspath__") or isinstance(obj, Path):
                return str(obj)
            try:
                return super().default(obj)
            except TypeError:
                return str(obj)

    def save(self, topic: str, step: str, data: dict) -> None:
        if not self.enabled:
            return
        with CheckpointManager._lock:
            body = self._read_raw(topic)  # must use _read_raw, NOT get() (TTL bypass)
            body[step] = {**data, "ts": datetime.now().isoformat()}
            p = self._path(topic)
            tmp = p.with_suffix(p.suffix + ".tmp")
            # Atomic write: write to temp file then replace
            # Bug 48: Use custom encoder to serialize Path and other objects gracefully
            tmp.write_text(
                json.dumps(body, indent=2, ensure_ascii=False, cls=self._CustomEncoder),
                encoding="utf-8",
            )

            # Bug 7: Retry loop for Windows Defender PermissionError
            for attempt in range(5):
                try:
                    # ENDURANCE: Maintain a .bak copy of the last known good state
                    if p.exists():
                        bak_path = p.with_suffix(".bak")
                        shutil.copy2(p, bak_path)

                    tmp.replace(p)
                    break
                except PermissionError:
                    if attempt == 4:
                        log.exception(f"Failed to replace {p} after 5 attempts")
                        # Clean up .tmp file on permanent failure
                        try:
                            if tmp.exists():
                                tmp.unlink()
                        except Exception:
                            pass
                        raise
                    time.sleep(0.5)

    def clear(self, topic: str) -> None:
        with CheckpointManager._lock:
            p = self._path(topic)
            if p.exists():
                p.unlink()
                log.info(f"Cleared: {p}")
            # P4-19 fix: also remove orphaned sibling files (.bak, .tmp, .corrupt.*)
            # that accumulate from atomic writes and corruption recovery.
            stem = p.stem
            for sibling in p.parent.glob(f"{stem}.*"):
                if sibling == p:
                    continue
                _is_cleanable = sibling.suffix in (".bak", ".tmp") or sibling.name.startswith(
                    f"{p.name}.corrupt."
                )
                if _is_cleanable:
                    try:
                        sibling.unlink()
                        log.info(f"Cleared sibling: {sibling}")
                    except Exception as _e:
                        log.debug(f"Could not remove sibling {sibling}: {_e}")

    def delete(self, topic: str) -> None:
        """Alias for clear() to satisfy Director retry actions."""
        self.clear(topic)


def build_checkpoint_manager(config: dict) -> CheckpointManager:
    cp = config.get("checkpoint", {})
    return CheckpointManager(
        Path(cp.get("dir", "studio_checkpoints")),
        cp.get("enabled", True),
        cp.get("max_age_hours", 0),
    )
