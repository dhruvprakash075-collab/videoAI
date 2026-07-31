"""memory_sync.py - MemorySyncMixin: long-term memory extraction + worldstate sync.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class MemorySyncMixin:
    """Memory: segment memory review and worldstate continuity sync."""

    # Provided by the composing DirectorAgent (LlmShimsMixin + PromptsMixin)
    _call_ollama: Any
    _parse_json: Any

    def review_segment_memory(
        self,
        segment_script: str,
        image_plan: dict,
        generated_prompts: list[str],
        current_memory: dict,
        world_state: str,
        generated_images: list[str] | None = None
    ) -> dict:
        """Review the segment and extract long-term memory items."""
        log.info("[DIRECTOR] Performing segment memory review...")

        images_block = ""
        if generated_images:
            images_block = "\nGenerated Images:\n" + "\n".join(f"  - {p}" for p in generated_images) + "\n"

        prompt_text = (
            f"You are the Creative Director. Analyze the segment to extract important visual and story memory.\n"
            f"Segment Script: {segment_script}\n"
            f"Image Plan: {image_plan}\n"
            f"Generated Prompts: {generated_prompts}\n"
            f"{images_block}"
            f"Current Memory: {current_memory}\n"
            f"World State: {world_state}\n\n"
            f"Identify important faces, outfits, jewelry, weapons, lore objects, locations, and story-impacting details.\n"
            f"Return a JSON object with 'memory_items' list. Each item must have:\n"
            f"{{'type': 'costume|face_reference|weapon|jewelry|lore_object|location|symbol_motif|relationship|timeline_change|negative_memory|character_identity|temporary_scene_detail',\n"
            f" 'name': '...', 'owner': '...', 'importance': 'core|high|medium', 'scope': 'project|story',\n"
            f" 'description': '...', 'visual_rules': [], 'negative_rules': [], 'lora_candidate': bool, 'reason': '...'}}"
        )

        res = self._call_ollama(prompt_text, format_json=True)
        return self._parse_json(res, {"memory_items": []})

    def _sync_memory_to_worldstate(self, topic: str, config: dict) -> None:
        """Sync character/lore to world state for continuity."""
        from memory.memory import WorldState

        ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
        ws = WorldState(topic=topic, checkpoint_dir=ck_dir)

        # Add config characters
        for _c_key, c_data in config.get("characters", {}).items():
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

        # Add production notes/recommendations
        p_notes = config.get("production_notes", {})
        if isinstance(p_notes, dict):
            for rec in p_notes.get("recommendations", []):
                if rec and rec not in ws._data.get("world_facts", []):
                    ws._data.setdefault("world_facts", []).append(f"[Director] {rec}")

        ws._save()
