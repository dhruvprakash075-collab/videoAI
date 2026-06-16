"""memory.py - Story memory for maintaining narrative context across segments."""

import contextlib
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level lock shared across all StoryMemory instances that write to the
# same shared story_memory.json file.  Per-instance locks are kept for
# read-modify-write consistency within a single instance, but the module-level
# lock prevents torn writes when multiple instances (e.g. parallel segments)
# race on the same file.
_story_memory_lock = threading.Lock()


class StoryMemory:
    """Persistent story memory tracking narrative context across segments.

    Each topic gets its own JSON file storing per-segment summaries.
    """

    def __init__(self, memory_file: Path):
        self._lock = threading.RLock()
        self.memory_file = memory_file
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict:
        with self._lock:
            if not self.memory_file.exists():
                return {}
            try:
                return json.loads(self.memory_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception) as e:
                log.warning(f"Corrupt memory file: {e}")
                return {}

    def _save_all(self, data: dict) -> None:
        # Acquire the module-level lock first so that concurrent instances
        # writing to the same file are serialized, then the per-instance lock
        # for internal consistency.
        with _story_memory_lock, self._lock:
            tmp_path = self.memory_file.with_suffix(".tmp")
            try:
                tmp_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                os.replace(tmp_path, self.memory_file)
            except Exception:
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
                raise

    def save(self, topic: str, segment: int, script: str, summary: str) -> None:
        with self._lock:
            data = self._load_all()
            if topic not in data:
                data[topic] = {"segments": []}
            # Remove any existing record for this segment to avoid duplicates on resume/retry
            data[topic]["segments"] = [
                s for s in data[topic]["segments"] if s.get("segment") != segment
            ]
            data[topic]["segments"].append(
                {
                    "segment": segment,
                    "script": script,
                    "summary": summary,
                }
            )
            self._save_all(data)
            log.info(f"Memory saved: {topic} seg {segment}")

    def load(self, topic: str) -> str:
        with self._lock:
            data = self._load_all()
            topic_data = data.get(topic, {})
            segments = topic_data.get("segments", [])
            if not segments:
                return ""
            recent = segments[-3:]
            context = "\n".join(f"Segment {s['segment']}: {s['summary']}" for s in recent)
            return context

    def get_all_entries(self, topic: str) -> list:
        """Return all stored entries for a topic as a list of {segment, script, summary} dicts.

        Used by ContextWindowManager.build_context_for_prompt() which expects a
        List[Dict] (one entry per segment) rather than the formatted string that
        ``load()`` returns.
        """
        with self._lock:
            data = self._load_all()
            topic_data = data.get(topic, {})
            return list(topic_data.get("segments", []))

    def read(self) -> dict:
        """Return the entire memory store as a dict (all topics, all entries).

        Used by segment_runner.do_memory_review to pass full context
        to the director agent.
        """
        return self._load_all()

    def clear(self, topic: str) -> None:
        with self._lock:
            data = self._load_all()
            data.pop(topic, None)
            self._save_all(data)
            log.info(f"Memory cleared: {topic}")


def build_context(memory_str: str) -> str:
    """Wrap memory string into a prompt context block."""
    if not memory_str:
        return ""
    return f"[Previous story context]\n{memory_str}\n[/Previous story context]\n"


class WorldState:
    """Tracks structured world facts, character states, and plot threads across segments.

    Persists to studio_checkpoints/world_state_{safe_topic}.json.
    On resume, the same file is reloaded so facts are never lost.

    Schema:
        characters: dict  - {char_name: {"traits": [...], "relationships": {...}, "status": str}}
        world_facts: list - ["The Fog cannot be dispelled by ordinary means", ...]
        open_threads: list - ["Who is the mysterious figure watching from the shadows?", ...]
        resolved_threads: list - ["The village elder's secret has been revealed", ...]
    """

    def __init__(self, topic: str, checkpoint_dir: Path):
        self._lock = threading.RLock()
        safe = topic.lower().replace(" ", "_")[:40]
        self._path = checkpoint_dir / f"world_state_{safe}.json"
        self._data: dict = self._load()
        log.info(
            f"[WorldState] Loaded from {self._path} - "
            f"{len(self._data.get('world_facts', []))} facts, "
            f"{len(self._data.get('open_threads', []))} open threads"
        )

    # -- persistence ----------------------------------------------------------

    def _load(self) -> dict:
        with self._lock:
            if self._path.exists():
                try:
                    return json.loads(self._path.read_text(encoding="utf-8"))
                except Exception as e:
                    log.warning(f"[WorldState] Corrupt state file: {e} - starting fresh")
            return {"characters": {}, "world_facts": [], "open_threads": [], "resolved_threads": []}

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            import os

            os.replace(tmp_path, self._path)

    # -- update ---------------------------------------------------------------

    def update(
        self, script: str, plan: dict, force_save: bool = True, config: dict | None = None
    ) -> None:
        """Extract and persist world facts from a newly written segment script.

        B3: When memory.llm_world_state is true, uses the 3B reviewer LLM for
        Devanagari-aware extraction. Falls back to the existing regex on any failure.
        Set force_save=False to skip redundant saves when resuming from checkpoint.
        """
        import re

        with self._lock:
            seg_num = plan.get("seg", 0)
            mood = plan.get("mood", "")
            title = plan.get("title", "")

            # ── B3: LLM-based extraction (Devanagari-aware) ───────────────
            _llm_used = False
            if config and config.get("memory", {}).get("llm_world_state", False):
                try:
                    from utils.specialized_models import extract_world_state

                    _llm_result = extract_world_state(script, config)
                    if _llm_result:
                        # Merge LLM-extracted characters
                        for name in _llm_result.get("characters", []):
                            if name and name not in self._data["characters"]:
                                self._data["characters"][name] = {
                                    "first_seen_seg": seg_num,
                                    "moods_seen": [mood] if mood else [],
                                    "status": "active",
                                }
                        # Merge facts (cap at 3 new per segment)
                        for fact in _llm_result.get("facts", [])[:3]:
                            if fact and fact not in self._data["world_facts"] and len(fact) < 200:
                                self._data["world_facts"].append(fact)
                        self._data["world_facts"] = self._data["world_facts"][-30:]
                        # Merge open threads
                        for thread in _llm_result.get("open_threads", [])[:2]:
                            if thread and thread not in self._data["open_threads"]:
                                self._data["open_threads"].append(thread)
                        self._data["open_threads"] = self._data["open_threads"][-10:]
                        # Merge resolved threads
                        for thread in _llm_result.get("resolved_threads", [])[:2]:
                            if thread and thread not in self._data.get("resolved_threads", []):
                                self._data.setdefault("resolved_threads", []).append(thread)
                        _llm_used = True
                        log.debug(
                            f"[B3] WorldState updated via LLM: "
                            f"{len(_llm_result.get('characters', []))} chars, "
                            f"{len(_llm_result.get('facts', []))} facts"
                        )
                except Exception as _b3_err:
                    log.warning(
                        f"[B3] LLM world-state extraction failed ({_b3_err}), falling back to regex"
                    )

            if not _llm_used:
                # ── Regex fallback (original behavior) ─────────────────
                # -- 1. Extract character mentions -----------------------------
                # Look for proper nouns (capitalized words) in the script.
                # P3-22: Unicode-aware — also match Devanagari-initial words
                # (U+0900–U+097F covers the full Devanagari block).
                char_candidates = re.findall(
                    r"\b(?:[A-Z][a-z]{2,}|[\u0900-\u097F][\u0900-\u097F\u200C\u200D]{2,})\b", script
                )
                exclusions = {
                    "The",
                    "But",
                    "And",
                    "For",
                    "Yet",
                    "So",
                    "He",
                    "She",
                    "It",
                    "They",
                    "Then",
                    "When",
                    "While",
                    "Where",
                    "Why",
                    "How",
                    "What",
                    "Suddenly",
                    "Meanwhile",
                    "Slowly",
                    "Quickly",
                    "This",
                    "That",
                    "There",
                    "Here",
                    "A",
                    "An",
                    "In",
                    "On",
                    "At",
                    "To",
                    "From",
                    "By",
                    "With",
                }
                # P-aud #6: A single capitalized word is usually not a real
                # recurring character (sentence-initial words, place names,
                # exclamations, etc.). Require a candidate to appear at least
                # twice before registering it as a *new* character. Characters
                # already known are always updated so their moods stay current.
                from collections import Counter

                _candidate_counts = Counter(char_candidates)
                for name in char_candidates:
                    if name in exclusions:
                        continue
                    known = name in self._data["characters"]
                    if not known and _candidate_counts[name] < 2:
                        continue
                    if not known:
                        self._data["characters"][name] = {
                            "first_seen_seg": seg_num,
                            "moods_seen": [],
                            "status": "active",
                        }
                    char_entry = self._data["characters"][name]
                    if mood and mood not in char_entry.get("moods_seen", []):
                        char_entry.setdefault("moods_seen", []).append(mood)

                # P3-22: also key continuity on full character names from the plan
                plan_chars = plan.get("characters", [])
                if isinstance(plan_chars, list):
                    for char_entry_item in plan_chars:
                        if isinstance(char_entry_item, dict):
                            full_name = char_entry_item.get("name", "").strip()
                        else:
                            full_name = str(char_entry_item).strip()
                        if full_name and full_name not in self._data["characters"]:
                            self._data["characters"][full_name] = {
                                "first_seen_seg": seg_num,
                                "moods_seen": [],
                                "status": "active",
                            }

                # -- 2. Extract world facts -----------------------------------
                fact_patterns = [
                    r"[A-Z][^.!?]{0,200}(?:cannot|never|always|must|forbidden|ancient|cursed|sacred|only|mysterious|strange|hidden|secret)[^.!?]{0,100}[.!?]",
                ]
                new_facts = []
                for pat in fact_patterns:
                    for m in re.findall(pat, script):
                        fact = m.strip()
                        if fact and fact not in self._data["world_facts"] and len(fact) < 200:
                            new_facts.append(fact)
                self._data["world_facts"].extend(new_facts[:3])
                self._data["world_facts"] = self._data["world_facts"][-30:]

                # -- 3. Every 5th segment: scan for open threads --------------
                if seg_num % 5 == 0 and seg_num > 0:
                    question_sentences = re.findall(r"[A-Z][^.!?]*\?", script)
                    for q in question_sentences[:2]:
                        q = q.strip()
                        if q and q not in self._data["open_threads"]:
                            self._data["open_threads"].append(q)
                    self._data["open_threads"] = self._data["open_threads"][-10:]

            # -- 4. Track key event (always, regardless of LLM/regex path) ---
            key_event = plan.get("key_event", "")
            if key_event:
                entry = f"[Seg {seg_num} - {title}] {key_event}"
                if entry not in self._data["world_facts"]:
                    self._data["world_facts"].append(entry)
                    # P-aud #7: this branch runs on every segment (not just the
                    # regex fallback), so cap here too or world_facts grows
                    # without bound on long LLM-extraction runs.
                    self._data["world_facts"] = self._data["world_facts"][-30:]

            if force_save:
                self._save()
            log.debug(
                f"[WorldState] Updated: {len(self._data['world_facts'])} facts, "
                f"{len(self._data['open_threads'])} threads"
            )

    # -- prompt injection ------------------------------------------------------

    def to_prompt_block(self, max_facts: int = 8, max_threads: int = 4) -> str:
        """Return a structured constraint block for injection into the writer LLM prompt.

        Keeps the block short (= ~500 tokens) to save context budget.
        """
        with self._lock:
            lines = ["[World State - Hard Constraints for this segment]"]

            facts = self._data.get("world_facts", [])[-max_facts:]
            if facts:
                lines.append("Established facts (do NOT contradict):")
                for f in facts:
                    lines.append(f"  • {f}")

            threads = self._data.get("open_threads", [])[-max_threads:]
            if threads:
                lines.append("Open plot threads (you may develop but not arbitrarily resolve):")
                for t in threads:
                    lines.append(f"  ? {t}")

            chars = self._data.get("characters", {})
            active_chars = [name for name, info in chars.items() if info.get("status") == "active"][
                :6
            ]
            if active_chars:
                lines.append(f"Active characters: {', '.join(active_chars)}")

            lines.append("[/World State]")
            return "\n".join(lines)
