"""story_planner.py - Plan multi-segment story outline and build segment prompts."""

import json
import logging
import re

from crewai import Crew, Task
from crewai.process import Process
from pydantic import BaseModel, Field

from utils.crewai_breaker import BreakerOpen, guarded_crewai_kickoff


class SegmentPlan(BaseModel):
    seg: int = Field(..., description="Segment number (1-indexed)")
    title: str = Field(..., description="Short title (max 5 words)")
    summary: str = Field(..., description="2-3 sentence summary of the segment")
    key_event: str = Field(..., description="The main event in this segment")
    mood: str = Field(..., description="One of: mysterious, horror, action, dramatic, calm, epic")
    num_images: int = Field(..., description="How many unique images are needed to visually cover this segment (integer, 4-12)")
    char_presence: list[dict[str, float]] = Field(..., description="A list of dictionaries. Each dictionary represents a frame and maps character IDs to their visual weight (0.0 to 1.0). The list length MUST match num_images.")
    target_word_count: int = Field(..., description="Target word count for this segment script (e.g., 200-300 for slow lore, 80-150 for fast action)")
    segment_duration: float = Field(..., description="How many seconds this segment should last (e.g., 45.0 for fast, 90.0 for slow). Based on word count and pacing.")

class StoryOutline(BaseModel):
    segments: list[SegmentPlan] = Field(..., description="List of segment plans in chronological order")

log = logging.getLogger(__name__)


def plan_story(topic: str, n_segments: int, config: dict,
               agent) -> list[dict]:
    """Generate a story outline spanning n_segments.

    For long videos (>30 segments), plans in batches to avoid LLM token limits.
    Each batch builds on the previous one's context.

    Returns list of dicts, one per segment.
    """
    BATCH_SIZE = 25  # max segments per LLM call to stay within output token limits

    if n_segments <= BATCH_SIZE:
        return _plan_batch(topic, 1, n_segments, n_segments, config, agent, "")

    # Batch planning for long videos (3 hours = 90 segments)
    all_outline = []
    for batch_start in range(1, n_segments + 1, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE - 1, n_segments)
        batch_size = batch_end - batch_start + 1

        # Build context from previous batches
        prev_context = ""
        if all_outline:
            last_3 = all_outline[-3:]
            prev_context = "Previous segments ended with:\n" + "\n".join(
                f"  Seg {s['seg']}: {s['title']} — {s['key_event']}" for s in last_3
            )

        log.info(f"Planning batch: segments {batch_start}-{batch_end} ({batch_size} segs)")
        batch = _plan_batch(topic, batch_start, batch_size, n_segments, config, agent, prev_context)

        # Renumber segments to be globally correct
        for i, seg in enumerate(batch):
            seg["seg"] = batch_start + i

        all_outline.extend(batch)

    log.info(f"Story plan complete: {len(all_outline)} segments in {(n_segments + BATCH_SIZE - 1) // BATCH_SIZE} batches")
    return all_outline


def _plan_batch(topic: str, batch_start: int, batch_size: int, total_segs: int,
                config: dict, agent, prev_context: str = "") -> list[dict]:
    """Plan a single batch of segments."""
    style = config.get("visual", {}).get("style", "Gothic Horror")

    world_lore = config.get("world_lore", {})
    lore_desc = world_lore.get("description", "")
    lore_rules = "\n".join(f"- {r}" for r in world_lore.get("rules", []))
    plot_threads = "\n".join(f"- {t}" for t in config.get("active_plot_threads", []))

    lore_section = f"World Lore: {lore_desc}\nUnbreakable Rules:\n{lore_rules}\n" if lore_desc or lore_rules else ""
    plot_section = f"Active Plot Threads to include:\n{plot_threads}\n" if plot_threads else ""
    context_section = f"\n{prev_context}\n" if prev_context else ""

    words_per_seg = config.get("script", {}).get("words_per_segment", 130)

    prompt = (
        f"Plan segments {batch_start}-{batch_start + batch_size - 1} of a {total_segs}-part lore video about '{topic}' "
        f"in {style} style.\n\n"
        f"{lore_section}"
        f"{plot_section}"
        f"{context_section}\n"
        f"Output EXACTLY {batch_size} segments.\n\n"
        f"CRITICAL: Assign a 'target_word_count' to each segment based on pacing. "
        f"For slow, lore-heavy, or dramatic scenes, use ~250-400 words. "
        f"For fast-paced action or intense tension scenes, use ~80-150 words. "
        f"The average target is {words_per_seg} words.\n\n"
        f"CRITICAL: Assign 'num_images' per segment based on content and duration. "
        f"This is the number of unique visual shots/images needed to cover the segment.\n"
        f"  - Dialogue/exposition scenes (slow): 4-5 images\n"
        f"  - Standard narrative scenes: 6-8 images\n"
        f"  - Action/battle/climax scenes: 8-12 images\n"
        f"  - Short segments (<60s): 4-5 images\n"
        f"  - Long segments (>90s): 8-12 images\n"
        f"  - More images = faster cuts = more energy. Fewer images = slower = more contemplative.\n"
        f"Match image count to the emotional intensity and duration of each segment.\n\n"
        f"CRITICAL: Assign a 'char_presence' (a JSON array of dictionaries). "
        f"The list length MUST equal num_images. Each dictionary maps character IDs "
        f"(e.g. {list(config.get('characters', {}).keys())}) to their visual weight (0.0 to 1.0):\n"
        f"  - 0.0 to 0.2: pure environment shot (character not visible or barely visible)\n"
        f"  - 0.3 to 0.4: environment-dominant (character very small or in background)\n"
        f"  - 0.5 to 0.7: balanced (both character and environment clearly visible)\n"
        f"  - 0.8 to 1.0: character-dominant portrait (medium or close-up)\n"
        f"Ensure variety in each segment: do not make all frames have the same weights. "
        f"The first frame of a segment should typically have lower weights for an establishing shot.\n\n"
        f"CRITICAL: Assign a segment_duration in seconds per segment. "
        f"Slow/lore-heavy: 80-120s. Fast/action: 30-50s. Moderate: 50-80s. Match duration to story rhythm."
    )

    try:
        crew = Crew(
            agents=[agent],
            tasks=[Task(
                description=prompt,
                agent=agent,
                expected_output=f"A structured StoryOutline containing exactly {batch_size} segment plans",
                output_json=StoryOutline,
                cache=True,  # Cache LLM results to avoid redundant calls
            )],
            process=Process.sequential,
            cache=True,      # Crew-level caching
            verbose=False,   # Reduce logging overhead
        )
        # Task 2: route through circuit-breaker-protected kickoff so a hung
        # outline LLM call fails fast instead of blocking the whole pipeline.
        # Falls back to defaults on BreakerOpen.
        try:
            from utils.concurrency import crewai_lock
        except ImportError:
            crewai_lock = None
        try:
            result = guarded_crewai_kickoff(
                crew,
                model_name=str(getattr(agent.llm, "model", "outline-planner")),
                timeout_s=240.0,
                lock=crewai_lock,
            )
        except BreakerOpen as _bo:
            log.warning(f"Story planner breaker open — using defaults: {_bo}")
            return _default_outline(topic, batch_size)

        if not result or (hasattr(result, "raw") and not result.raw.strip()):
            log.warning("CrewAI returned empty result - using default outline")
            return _default_outline(topic, batch_size)

        if hasattr(result, "json_dict") and result.json_dict and "segments" in result.json_dict:
            outline = result.json_dict["segments"]
        elif hasattr(result, "pydantic") and result.pydantic:
            outline = [s.model_dump() for s in result.pydantic.segments]
        else:
            raw = result.raw if hasattr(result, "raw") else str(result)
            outline = _parse_outline(raw, batch_size)

        # Enforce the requested batch size: the LLM sometimes returns more or
        # fewer segments than asked. Truncate extras; pad shortfalls with defaults
        # so the count is exactly batch_size (honors a locked segment_count).
        if isinstance(outline, list):
            if len(outline) > batch_size:
                log.info(f"Outline returned {len(outline)} segments; trimming to requested {batch_size}")
                outline = outline[:batch_size]
            elif len(outline) < batch_size:
                log.info(f"Outline returned {len(outline)} segments; padding to requested {batch_size}")
                _defaults = _default_outline(topic, batch_size)
                outline = outline + _defaults[len(outline):batch_size]

        return outline
    except Exception as e:
        log.warning(f"Story planning batch failed ({e}) — using defaults")
        return _default_outline(topic, batch_size)


def build_segment_prompt(plan: dict, context: str, total_segs: int,
                         words_per_seg: int,
                         world_state_block: str = "",
                         narrator_persona: str = "") -> str:
    """Build a prompt for writing a single segment's script.

    Args:
        plan: Segment plan dict with title, summary, key_event, mood
        context: Previous story context from StoryMemory / ContextWindowManager
        total_segs: Total number of segments
        words_per_seg: Target word count per segment
        world_state_block: Optional world-state constraint block from WorldState.to_prompt_block()
        narrator_persona: The persona/voice the writer should adopt.

    Returns:
        Prompt string for the CrewAI agent
    """
    world_section = f"\n{world_state_block}\n" if world_state_block else ""
    persona_section = f"\nNARRATOR PERSONA (Adopt this voice strictly):\n{narrator_persona}\n" if narrator_persona else ""

    return (
        f"Write segment {plan.get('seg', 1)}/{total_segs} of a lore video.\n\n"
        f"Title: {plan.get('title', 'Untitled')}\n"
        f"Summary: {plan.get('summary', '')}\n"
        f"Key Event: {plan.get('key_event', '')}\n"
        f"Mood: {plan.get('mood', 'mysterious')}\n\n"
        f"{context}\n"
        f"{world_section}"
        f"{persona_section}\n"
        f"Write EXACTLY {words_per_seg} words. "
        "Structure: Hook (1 sentence) -> Escalation (2-3 sentences) "
        "-> Insight (1 sentence).\n"
        "CRITICAL: Make the narration highly emotional and dramatic! Use expressive punctuation like ellipses (...) for dramatic pauses, exclamation marks (!), and rhetorical questions (?). This forces the voice synthesizer to read it with intense emotion instead of sounding flat.\n"
        "OUTPUT FORMAT: Output the narration script using [narration] ... [/narration] blocks.\n"
        "Each block should contain precisely the words to be spoken. Wrap section headers in [section] tags.\n"
        "Use [pause] tags for dramatic beats. Keep cinematic pacing — slow, atmospheric."
    )


def _parse_outline(raw: str, expected: int) -> list[dict]:
    """Parse JSON outline from LLM response with fallback.

    Uses bracket depth tracking to find the outermost JSON array,
    which correctly handles nested arrays like char_presence.
    Accepts outlines within 2 of expected length to be lenient.
    """
    try:
        # Clean markdown code blocks if present
        clean_raw = raw.strip()
        if clean_raw.startswith("```"):
            lines = clean_raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean_raw = "\n".join(lines).strip()

        # Try direct JSON parsing
        try:
            data = json.loads(clean_raw)
            if isinstance(data, list) and abs(len(data) - expected) <= 2:
                return data[:expected] if len(data) > expected else data
        except json.JSONDecodeError:
            pass

        # Use bracket depth tracking to find outermost JSON array
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "[":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = raw[start:i+1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, list) and abs(len(data) - expected) <= 2:
                            return data[:expected] if len(data) > expected else data
                    except json.JSONDecodeError:
                        start = -1

        # Fall back to regex search for any JSON array
        for match in re.finditer(r"\[.*\]", raw, re.DOTALL):
            try:
                data = json.loads(match.group())
                if isinstance(data, list) and abs(len(data) - expected) <= 2:
                    return data[:expected] if len(data) > expected else data
            except json.JSONDecodeError:
                continue
    except Exception as e:
        log.warning(f"Outline parse failed: {e}")

    return _default_outline("story", expected)


def _default_outline(topic: str, n: int) -> list[dict]:
    """Generate a default outline when LLM planning fails."""
    moods = ["mysterious", "horror", "action", "dramatic", "calm", "epic"]
    _num_images = 6
    return [
        {
            "seg": i + 1,
            "title": f"Part {i + 1}",
            "summary": f"Chapter {i + 1} of {topic}",
            "key_event": f"Event unfolds in chapter {i + 1}",
            "mood": moods[i % len(moods)],
            "num_images": _num_images,
            # P4-17 fix: build char_presence with exactly num_images entries (not
            # hardcoded 6 empty dicts).  Seed the first frame as a low-weight (0.1)
            # environment shot so the env-ratio enforcement has something to work with.
            "char_presence": [{"env": 0.1}] + [{} for _ in range(_num_images - 1)],
            "target_word_count": 200,
            "segment_duration": 60.0,
        }
        for i in range(n)
    ]
