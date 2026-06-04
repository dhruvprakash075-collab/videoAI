"""project_store.py - Three-tier memory: ProjectStore, StoryStore, and compat shim.

Layout on disk:
    studio_projects/
        {project}/
            project.json        # shared across stories: characters, world, motifs, visual_locks
            stories/
                {story}/
                    story.json  # per-story: segments, arc, decision record ref
                    audit.json  # continuity/audit log for this story

One-time-use runs write only to an isolated story store and never touch project.json.

Backward-compat: if studio_checkpoints/{topic}_memory.json exists and no project store
does, it is loaded as a one-time story store.
"""

import contextlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECTS_ROOT = Path("studio_projects")


def _safe(name: str, maxlen: int = 60) -> str:
    # P4-30 fix: use Unicode-aware regex so Devanagari characters are preserved.
    # The old [^a-zA-Z0-9_\-] pattern stripped all non-ASCII, causing filename
    # collisions for distinct Hindi topics.  We allow \w (Unicode letters/digits)
    # plus Devanagari combining marks (U+0900–U+097F) and hyphens.
    return re.sub(r"[^\w\u0900-\u097F\-]", "_", name, flags=re.UNICODE)[:maxlen]


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log.exception(f"Atomic write failed for {path}: {e}")
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
        raise


def _load_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Could not load {path}: {e}")
        return default


# ══════════════════════════════════════════════════════════════════════════════
# ProjectStore — shared continuity across stories in a project
# ══════════════════════════════════════════════════════════════════════════════


class ProjectStore:
    """Shared continuity store for a named project.

    Stores characters, world lore, recurring motifs, and visual locks.
    Shared across all stories in the project.
    """

    def __init__(self, project_name: str, root: Path = PROJECTS_ROOT):
        self._lock = threading.RLock()
        self._dir = root / _safe(project_name)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "project.json"
        self._data: dict = self._load()
        log.info(
            f"[ProjectStore] '{project_name}' loaded — "
            f"{len(self._data.get('characters', {}))} chars, "
            f"{len(self._data.get('visual_locks', {}))} visual locks"
        )

    def _load(self) -> dict:
        with self._lock:
            data = _load_json(self._path, {})
            for key in ("characters", "motifs", "world_lore", "visual_locks"):
                data.setdefault(key, {})
            return data

    def _save(self) -> None:
        with self._lock:
            _atomic_write(self._path, self._data)

    # ── Characters ────────────────────────────────────────────────────────

    def log_character(
        self,
        name: str,
        visual_description: str,
        voice_reference: str = "",
        portrait_prompt: str = "",
    ) -> None:
        with self._lock:
            key = name.lower().replace(" ", "_")
            existing = self._data["characters"].get(key, {})
            self._data["characters"][key] = {
                "name": name,
                "visual_description": visual_description,
                "voice_reference": voice_reference,
                "portrait_prompt": portrait_prompt or existing.get("portrait_prompt", ""),
                "master_portrait_path": existing.get("master_portrait_path", ""),
                "master_portrait_hash": existing.get("master_portrait_hash", ""),
                "updated_at": time.time(),
            }
            log.info(f"[ProjectStore] Character logged: {name}")
            self._save()

    def get_character(self, name: str) -> dict | None:
        with self._lock:
            key = name.lower().replace(" ", "_")
            entry = self._data["characters"].get(key)
            if entry is None:
                return None
            return dict(entry)

    # ── Master portrait (Bonsai IP-Adapter reference) ────────────────────

    def set_master_portrait(
        self,
        char_key: str,
        path: str,
        content_hash: str = "",
    ) -> None:
        """Store the path (and optional content hash) of a character's master portrait.

        The hash is used in cache keys so regenerating the portrait invalidates
        all cached frames that referenced the previous portrait.
        """
        with self._lock:
            char = self._data["characters"].get(char_key)
            if char is None:
                log.warning(
                    f"[ProjectStore] set_master_portrait skipped — unknown char '{char_key}'"
                )
                return
            char["master_portrait_path"] = path
            char["master_portrait_hash"] = content_hash
            char["updated_at"] = time.time()
            log.info(f"[ProjectStore] Master portrait set for '{char_key}': {path}")
            self._save()

    def get_master_portrait_path(self, char_key: str) -> str:
        """Return the path (relative or absolute) of the master portrait, or ''."""
        with self._lock:
            char = self._data["characters"].get(char_key)
            if not char:
                return ""
            return char.get("master_portrait_path", "")

    def get_master_portrait_hash(self, char_key: str) -> str:
        """Return the content hash of the master portrait, or '' if not set."""
        with self._lock:
            char = self._data["characters"].get(char_key)
            if not char:
                return ""
            return char.get("master_portrait_hash", "")

    def set_portrait_prompt(self, char_key: str, prompt: str) -> None:
        """Set the structured portrait-generation prompt for a character."""
        with self._lock:
            char = self._data["characters"].get(char_key)
            if char is None:
                log.warning(
                    f"[ProjectStore] set_portrait_prompt skipped — unknown char '{char_key}'"
                )
                return
            char["portrait_prompt"] = prompt
            char["updated_at"] = time.time()
            self._save()

    # ── Motifs ────────────────────────────────────────────────────────────

    def log_recurring_motif(self, motif_name: str, details: str) -> None:
        with self._lock:
            key = motif_name.lower().replace(" ", "_")
            self._data["motifs"][key] = {"name": motif_name, "details": details}
            self._save()

    # ── Visual locks (per-character appearance lock) ──────────────────────

    def set_visual_lock(
        self,
        char_key: str,
        description: str,
        seed: int | None = None,
        lora_path: str | None = None,
        provenance: str = "director",
    ) -> None:
        """Store a visual lock for a character (Req 13)."""
        with self._lock:
            if not description or len(description) < 20:
                log.info(
                    f"[ProjectStore] Visual lock skipped for '{char_key}' "
                    f"— description too sparse ({len(description)} chars)"
                )
                return
            self._data["visual_locks"][char_key] = {
                "description": description,
                "seed": seed,
                "lora_path": lora_path,
                "provenance": provenance,
                "updated_at": time.time(),
            }
            log.info(f"[ProjectStore] Visual lock set for '{char_key}'")
            self._save()

    def get_visual_lock(self, char_key: str) -> dict | None:
        with self._lock:
            entry = self._data["visual_locks"].get(char_key)
            if entry is None:
                return None
            return dict(entry)

    # ── World lore ────────────────────────────────────────────────────────

    def add_world_lore(self, key: str, value: str) -> None:
        with self._lock:
            self._data["world_lore"][key] = value
            self._save()

    def get_world_lore(self) -> dict:
        with self._lock:
            return dict(self._data.get("world_lore", {}))


# ══════════════════════════════════════════════════════════════════════════════
# StoryStore — per-story state (segments, arc, audit)
# ══════════════════════════════════════════════════════════════════════════════


class StoryStore:
    """Per-story state: segments, arc, and continuity audit log.

    For project runs: lives under studio_projects/{project}/stories/{story}/.
    For one-time runs: lives under studio_projects/_one_time/{story}/ and is
    never written to any project.json.
    """

    def __init__(
        self, story_name: str, project_name: str | None = None, root: Path = PROJECTS_ROOT
    ):
        self._lock = threading.RLock()
        if project_name:
            self._dir = root / _safe(project_name) / "stories" / _safe(story_name)
        else:
            # One-time-use: isolated, never touches project store
            self._dir = root / "_one_time" / _safe(story_name)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._story_path = self._dir / "story.json"
        self._audit_path = self._dir / "audit.json"
        self._data: dict = self._load_story()
        log.info(
            f"[StoryStore] '{story_name}' "
            f"({'project=' + project_name if project_name else 'one_time'}) — "
            f"{len(self._data.get('segments', []))} segments"
        )

    def _load_story(self) -> dict:
        with self._lock:
            data = _load_json(self._story_path, {})
            data.setdefault("segments", [])
            data.setdefault("world_facts", [])
            data.setdefault("open_threads", [])
            # characters and motifs are stored here for one-time runs
            data.setdefault("characters", {})
            data.setdefault("motifs", {})
            return data

    def _save_story(self) -> None:
        with self._lock:
            # Cap to prevent bloat
            if len(self._data.get("segments", [])) > 100:
                dropped = len(self._data["segments"]) - 100
                log.warning(
                    f"[StoryStore] Capping segments at 100; dropping {dropped} oldest entries "
                    f"from '{self._dir.name}'. Consider splitting into multiple stories "
                    "if this is unexpected."
                )
                self._data["segments"] = self._data["segments"][-100:]
            _atomic_write(self._story_path, self._data)

    # ── Segments ──────────────────────────────────────────────────────────

    def save_segment(self, segment: int, script: str, summary: str) -> None:
        with self._lock:
            self._data["segments"] = [
                s for s in self._data["segments"] if s.get("segment") != segment
            ]
            self._data["segments"].append(
                {"segment": segment, "script": script, "summary": summary}
            )
            self._save_story()

    def load_recent_context(self, n: int = 3) -> str:
        with self._lock:
            segs = self._data.get("segments", [])[-n:]
            return "\n".join(f"Segment {s['segment']}: {s['summary']}" for s in segs)

    # ── Continuity audit ──────────────────────────────────────────────────

    def check_continuity(
        self, segment_assets: dict, project_store: ProjectStore | None = None
    ) -> bool:
        """Audit a segment against project characters + story facts."""
        with self._lock:
            seg_num = segment_assets.get("seg_num", "?")
            target = " ".join(str(v) for v in segment_assets.values()).lower()

            violations = []
            chars = {}
            if project_store:
                chars = project_store._data.get("characters", {})

            for _key, info in chars.items():
                name = info.get("name", "").lower()
                first = name.split()[0] if name else ""
                if first and re.search(rf"\b{re.escape(first)}\b", target):
                    desc = info.get("visual_description", "").lower()
                    if "blue eyes" in desc and "red eyes" in target:
                        violations.append(
                            f"{info['name']}: blue eyes in memory but red eyes in prompt"
                        )
                    if "black hair" in desc and "blonde hair" in target:
                        violations.append(
                            f"{info['name']}: black hair in memory but blonde hair in prompt"
                        )

            audit = {
                "segment": seg_num,
                "passed": not violations,
                "violations": violations,
                "timestamp": time.time(),
            }
            audit_data = _load_json(self._audit_path, {"entries": []})
            audit_data["entries"].append(audit)
            if len(audit_data["entries"]) > 100:
                audit_data["entries"] = audit_data["entries"][-100:]
            _atomic_write(self._audit_path, audit_data)

            if violations:
                for v in violations:
                    log.warning(f"[CONTINUITY] Seg {seg_num}: {v}")
                return False
            log.info(f"[CONTINUITY] Seg {seg_num}: pass")
            return True


# ══════════════════════════════════════════════════════════════════════════════
# PermanentMemoryLog compatibility shim
# ══════════════════════════════════════════════════════════════════════════════


class PermanentMemoryLog:
    """Backward-compatible wrapper over ProjectStore + StoryStore.

    Existing callers (pipeline_long, local_ui, studio_tui) continue to work
    unchanged. Internally routes to the appropriate tier.

    For project runs: pass project_name.
    For one-time runs: omit project_name (default).

    One-time mode persistence:
        Characters and motifs are written to
        studio_checkpoints/_one_time_{safe_topic}/permanent_memory.json
        on every update so that a crashed run can resume with full continuity.
    """

    def __init__(
        self,
        topic: str = "default_topic",
        base_dir: str = "studio_checkpoints",
        project_name: str | None = None,
    ):
        self._topic = topic
        self._project_name = project_name
        self._lock = threading.RLock()

        # Determine if we have a legacy flat memory file to migrate
        legacy_path = Path(base_dir) / f"{_safe(topic)}_memory.json"

        if project_name:
            self._project = ProjectStore(project_name)
        else:
            self._project = None

        self._story = StoryStore(topic, project_name=project_name)

        # One-time mode: path for the dedicated permanent_memory.json checkpoint.
        # This is separate from StoryStore's story.json so that characters/motifs
        # survive a crash and can be reloaded on resume without touching the
        # project store.
        if not project_name:
            self._one_time_mem_path: Path | None = (
                Path(base_dir) / f"_one_time_{_safe(topic)}" / "permanent_memory.json"
            )
        else:
            self._one_time_mem_path = None

        # Migrate legacy flat file if present and story store is empty
        if legacy_path.exists() and not self._story._data.get("segments"):
            self._migrate_legacy(legacy_path)

        # Expose data dict for code that reads .data directly
        self.data = self._build_data_view()

        # One-time mode: load persisted characters/motifs from the checkpoint
        # directory so that a resumed run recovers continuity data.
        if self._one_time_mem_path and self._one_time_mem_path.exists():
            self._load_one_time_memory()

    def _load_one_time_memory(self) -> None:
        """Load characters/motifs from the one-time permanent_memory.json checkpoint.

        Called during __init__ when the file exists, so a resumed run recovers
        all character and motif data that was persisted in a previous (crashed) run.
        """
        try:
            saved = _load_json(self._one_time_mem_path, {})
            chars = saved.get("characters", {})
            motifs = saved.get("motifs", {})
            if chars or motifs:
                # Merge into the in-memory data view (don't overwrite newer story data)
                self.data["characters"].update(chars)
                self.data["motifs"].update(motifs)
                # Sync back into the story store so StoryStore is consistent
                self._story._data["characters"] = self.data["characters"]
                self._story._data["motifs"] = self.data["motifs"]
                log.info(
                    f"[PermanentMemoryLog] Resumed one-time memory from "
                    f"{self._one_time_mem_path}: "
                    f"{len(chars)} chars, {len(motifs)} motifs"
                )
        except Exception as e:
            log.warning(f"[PermanentMemoryLog] Could not load one-time memory on resume: {e}")

    def _migrate_legacy(self, legacy_path: Path) -> None:
        """Load legacy studio_checkpoints/{topic}_memory.json into the story store."""
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            for seg in legacy.get("segments", []):
                self._story.save_segment(
                    seg.get("segment", 0), seg.get("script", ""), seg.get("summary", "")
                )
            for key, char in legacy.get("characters", {}).items():
                name = char.get("name", key)
                desc = char.get("visual_description", "")
                voice = char.get("voice_reference", "")
                if self._project:
                    self._project.log_character(name, desc, voice)
            for key, motif in legacy.get("motifs", {}).items():
                if self._project:
                    self._project.log_recurring_motif(
                        motif.get("name", key), motif.get("details", "")
                    )
            log.info(f"[PermanentMemoryLog] Migrated legacy memory: {legacy_path}")
        except Exception as e:
            log.warning(f"[PermanentMemoryLog] Legacy migration failed: {e}")

    def _build_data_view(self) -> dict:
        """Build a data dict compatible with code that reads .data directly."""
        chars = {}
        motifs = {}
        if self._project:
            chars = self._project._data.get("characters", {})
            motifs = self._project._data.get("motifs", {})
        else:
            # One-time mode: characters and motifs are persisted inside story.json
            chars = self._story._data.get("characters", {})
            motifs = self._story._data.get("motifs", {})
        return {
            "characters": chars,
            "motifs": motifs,
            "segments": self._story._data.get("segments", []),
            "audit_log": [],
        }

    def _save_memory(self) -> None:
        """Sync .data changes back to the stores (for code that mutates .data directly)."""
        with self._lock:
            if self._project:
                for key, char in self.data.get("characters", {}).items():
                    self._project._data["characters"][key] = char
                self._project._save()
            else:
                # One-time mode: persist characters and motifs into the story store
                # so that resume works across restarts.
                self._story._data["characters"] = self.data.get("characters", {})
                self._story._data["motifs"] = self.data.get("motifs", {})
                self._story._save_story()
                # Also write to the dedicated one-time checkpoint so resume can
                # reload characters/motifs even if the story store is not yet
                # populated (e.g. crash before first segment is saved).
                if self._one_time_mem_path is not None:
                    try:
                        _atomic_write(
                            self._one_time_mem_path,
                            {
                                "characters": self.data.get("characters", {}),
                                "motifs": self.data.get("motifs", {}),
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"[PermanentMemoryLog] Could not write one-time memory checkpoint: {e}"
                        )
            for key, motif in self.data.get("motifs", {}).items():
                if self._project:
                    self._project._data["motifs"][key] = motif
                    self._project._save()

    # ── Public API (unchanged from original PermanentMemoryLog) ───────────

    def log_character(
        self,
        name: str,
        visual_description: str,
        voice_reference: str,
        portrait_prompt: str = "",
    ) -> None:
        if self._project:
            self._project.log_character(
                name, visual_description, voice_reference, portrait_prompt=portrait_prompt
            )
        else:
            key = name.lower().replace(" ", "_")
            existing = self.data["characters"].get(key, {})
            self.data["characters"][key] = {
                "name": name,
                "visual_description": visual_description,
                "voice_reference": voice_reference,
                "portrait_prompt": portrait_prompt or existing.get("portrait_prompt", ""),
                "master_portrait_path": existing.get("master_portrait_path", ""),
                "master_portrait_hash": existing.get("master_portrait_hash", ""),
            }
            # One-time mode: persist to story store and dedicated checkpoint
            with self._lock:
                self._story._data["characters"] = self.data["characters"]
                self._story._save_story()
                if self._one_time_mem_path is not None:
                    try:
                        _atomic_write(
                            self._one_time_mem_path,
                            {
                                "characters": self.data.get("characters", {}),
                                "motifs": self.data.get("motifs", {}),
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"[PermanentMemoryLog] Could not write one-time memory checkpoint: {e}"
                        )
        log.info(f"[PermanentMemoryLog] Character logged: {name}")

    def get_character(self, name: str) -> dict | None:
        if self._project:
            return self._project.get_character(name)
        key = name.lower().replace(" ", "_")
        entry = self.data["characters"].get(key)
        if entry is None:
            return None
        return dict(entry)

    def read(self) -> dict:
        """Return the full memory dict (characters, motifs, segments, audit_log).

        This was missing from the legacy PermanentMemoryLog API — callers had to
        reach into ``.data`` directly. ``read()`` is the documented, stable
        accessor for the same view.
        """
        with self._lock:
            return {
                "characters": dict(self.data.get("characters", {})),
                "motifs": dict(self.data.get("motifs", {})),
                "segments": list(self.data.get("segments", [])),
                "audit_log": list(self.data.get("audit_log", [])),
            }

    def log_recurring_motif(self, motif_name: str, details: str) -> None:
        if self._project:
            self._project.log_recurring_motif(motif_name, details)
        else:
            key = motif_name.lower().replace(" ", "_")
            self.data["motifs"][key] = {"name": motif_name, "details": details}
            # One-time mode: persist to story store and dedicated checkpoint
            with self._lock:
                self._story._data["motifs"] = self.data["motifs"]
                self._story._save_story()
                if self._one_time_mem_path is not None:
                    try:
                        _atomic_write(
                            self._one_time_mem_path,
                            {
                                "characters": self.data.get("characters", {}),
                                "motifs": self.data.get("motifs", {}),
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"[PermanentMemoryLog] Could not write one-time memory checkpoint: {e}"
                        )

    def check_continuity(self, segment_assets: dict) -> bool:
        return self._story.check_continuity(segment_assets, self._project)
