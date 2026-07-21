"""Director memory seeding — feed pre-production findings into StoryMemory + WorldState.

Called once after pre-production completes. Populates three persistence layers:

1. PermanentMemoryLog (director_knowledge) — survives across runs/series
2. WorldState (characters, world_facts, open_threads) — per-topic checkpoint
3. StoryMemory (segment 0 summary) — narrative context for Writer

All three are loaded on resume so the Director/Writer never lose context.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _seed_director_memory(topic: str, overlay: dict, config: dict) -> None:
    """Feed Director pre-production findings into StoryMemory + WorldState.

    Args:
        topic: Video topic (used as key in all three stores)
        overlay: Config overlay from Director (characters, vision, production_notes)
        config: Full pipeline config (for checkpoint/memory paths)
    """
    from memory import StoryMemory, WorldState
    from memory.permanent_memory import PermanentMemoryLog

    perm = PermanentMemoryLog(topic=topic)
    perm.data.setdefault("director_knowledge", {})

    for c_key, c_data in overlay.get("characters", {}).items():
        name = c_data.get("name", c_key)
        desc = c_data.get("description", "")
        if name and desc:
            perm.log_character(name, desc, "")
        perm.data["director_knowledge"][c_key] = {
            "name": name,
            "description": desc,
            "source": "director_pre_production",
        }

    vision = overlay.get("_director_vision", {})
    if vision.get("theme"):
        perm.log_recurring_motif("theme", vision["theme"])
    if vision.get("emotions"):
        perm.log_recurring_motif("emotions", vision["emotions"])
    perm.data["director_knowledge"]["production_notes"] = overlay.get("production_notes", {})
    perm._save_memory()

    ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
    ws = WorldState(topic=topic, checkpoint_dir=ck_dir)
    for c_data in overlay.get("characters", {}).values():
        name = c_data.get("name", "")
        desc = c_data.get("description", "")
        if name:
            ws._data.setdefault("characters", {})
            ws._data["characters"][name] = {
                "first_seen_seg": 0,
                "moods_seen": [],
                "status": "active",
                "description": desc,
            }
            fact = f"{name}: {desc[:150]}" if desc else f"Character: {name}"
            if fact not in ws._data.get("world_facts", []):
                ws._data.setdefault("world_facts", []).append(fact)

    _production_notes = overlay.get("production_notes", {})
    _custom_instruction = _production_notes.get("custom_instructions", "")
    if _custom_instruction:
        _instruction_fact = f"[Director instruction] {_custom_instruction}"
        if _instruction_fact not in ws._data.get("world_facts", []):
            ws._data.setdefault("world_facts", []).append(_instruction_fact)
    for rec in _production_notes.get("recommendations", []):
        if rec:
            _rec_fact = f"[Director] {rec}"
            if _rec_fact not in ws._data.get("world_facts", []):
                ws._data.setdefault("world_facts", []).append(_rec_fact)

    ws._save()
    log.info(
        f"[MEMORY] Director knowledge seeded: "
        f"{len(overlay.get('characters', {}))} characters, "
        f"{len(ws._data.get('world_facts', []))} facts"
    )

    mem_file = config.get("memory", {}).get("memory_file", "studio_checkpoints/story_memory.json")
    sm = StoryMemory(Path(mem_file))
    char_summaries = ", ".join(
        f"{c_data.get('name', k)}" for k, c_data in overlay.get("characters", {}).items()
    )
    theme = vision.get("theme", "Unknown")
    sm.save(
        topic,
        0,
        f"Director pre-production for {topic}. "
        f"Theme: {theme}. Characters: {char_summaries}. "
        f"Style: {vision.get('visual_style', '')}. "
        f"Emotions: {vision.get('emotions', '')}. ",
        f"Director Vision: {theme} — {char_summaries}",
    )
    log.info("[MEMORY] StoryMemory pre-seeded with Director context")
