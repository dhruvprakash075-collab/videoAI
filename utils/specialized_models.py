"""
specialized_models.py - Fast specialized models for pipeline optimization.

Uses small, purpose-built models for specific tasks:
- reviewer (Qwen2.5-0.5B default): Fast JSON extraction for world-state facts/
  characters/threads. Override via ``models.reviewer`` in config.
- image-engineer (Replete-LLM-V2.5-Qwen-7B): Detailed image prompt generation

These models run via Ollama and are much faster than using the Director/Writer models.
"""

import logging
import re

from utils.utils import extract_json

log = logging.getLogger(__name__)

# Model names in Ollama. Override per-deployment via ``models.reviewer`` in config.yaml.
SCRIPT_REVIEWER_MODEL = "qwen2.5:0.5b"
IMAGE_ENGINEER_MODEL = "image-engineer"


def _call_ollama(
    prompt: str, model: str, format_json: bool = False, temperature: float = 0.3, timeout: int = 60
) -> str | None:
    """Call Ollama via the shared ``OllamaClient`` (B1 breaker + retry).

    The ``timeout`` argument is retained for backward compatibility with
    existing call sites and test fixtures, but the actual per-request timeout
    now comes from ``ollama.request_timeout`` in the config (default 240s).
    The breaker ensures we fail fast when the model is unhealthy.
    """
    try:
        from config import load_config
        from utils.ollama_client import get_ollama_client

        config = load_config()
        client = get_ollama_client(config)
        text = client.generate(
            prompt,
            model=model,
            format_json=format_json,
            temperature=temperature,
            num_predict=4096,
        )
        return text or None
    except Exception as e:
        log.warning(f"[{model}] Call failed: {e}")
        return None


def review_script_fast(
    script: str, plan: dict, context: str = "", characters: dict | None = None
) -> dict:
    """
    Fast script review using qwen2.5:0.5b model.

    Returns:
        {
            "approved": bool,
            "quality_score": int (1-10),
            "issues": list[str],
            "suggestions": list[str],
            "rewrite_needed": bool,
            "rewrite_instructions": str
        }
    """
    mood = plan.get("mood", "mysterious")
    target_words = plan.get("target_word_count", 200)

    # Build character context
    char_lines = []
    if characters:
        for c_key, c_data in characters.items():
            name = c_data.get("name", c_key)
            desc = c_data.get("description", "")[:80]
            if name:
                char_lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    chars_block = "\n".join(char_lines) if char_lines else "No character details."

    prompt = f"""Review this script segment for quality issues.

SCRIPT:
{script}

CONTEXT:
- Segment mood: {mood}
- Expected word count: {target_words}
- Characters: {chars_block}
- Previous story: {context[:200] if context else "None"}

CHECK FOR:
1. Plot holes or logical inconsistencies
2. Character behavior inconsistencies
3. Pacing problems (too fast/slow for mood)
4. Emotional tone mismatches
5. Missing narrative elements (hook, escalation, resolution)
6. Word count within target range

OUTPUT ONLY VALID JSON:
{{
  "approved": true/false,
  "quality_score": 1-10,
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["suggestion 1"],
  "rewrite_needed": true/false,
  "rewrite_instructions": "specific instructions if rewrite needed"
}}"""

    response = _call_ollama(prompt, SCRIPT_REVIEWER_MODEL, format_json=True, temperature=0.3)

    if not response:
        # Reviewer unavailable — do NOT fabricate approval
        log.warning(
            "[reviewer] No response — reviewer unavailable, manual review recommended"
        )
        return {
            "approved": False,
            "review_unavailable": True,
            "quality_score": 0,
            "issues": [],
            "suggestions": [],
            "rewrite_needed": False,
            "rewrite_instructions": "",
            "feedback": "Reviewer unavailable — manual review recommended",
        }

    try:
        result = extract_json(response)
        if isinstance(result, dict):
            # Validate required fields
            result.setdefault("approved", True)
            result.setdefault("quality_score", 7)
            result.setdefault("issues", [])
            result.setdefault("suggestions", [])
            result.setdefault("rewrite_needed", False)
            result.setdefault("rewrite_instructions", "")
            return result
    except Exception:
        pass

    log.warning(f"[reviewer] Failed to parse JSON: {response[:100]}")
    return {
        "approved": False,
        "review_unavailable": True,
        "quality_score": 0,
        "issues": [],
        "suggestions": [],
        "rewrite_needed": False,
        "rewrite_instructions": "",
        "feedback": "Reviewer unavailable — manual review recommended",
    }


def generate_image_prompt(
    script: str, plan: dict, characters: dict | None = None, visual_style: str = ""
) -> str:
    """
    Generate detailed Stable Diffusion prompt using image-engineer model (7B).

    Returns:
        Detailed SD prompt string (comma-separated tags)
    """
    mood = plan.get("mood", "mysterious")
    key_event = plan.get("key_event", "")

    # Build character context
    char_lines = []
    if characters:
        for c_key, c_data in characters.items():
            name = c_data.get("name", c_key)
            desc = c_data.get("description", "")[:100]
            if name and desc:
                char_lines.append(f"- {name}: {desc}")
    chars_block = "\n".join(char_lines) if char_lines else "No character details."

    prompt = f"""Generate a Stable Diffusion image prompt for this story segment.

STORY SEGMENT:
{script[:500]}

CONTEXT:
- Mood: {mood}
- Key event: {key_event}
- Visual style: {visual_style or "dark fantasy anime"}
- Characters:
{chars_block}

RULES:
1. Output ONLY the prompt text, nothing else
2. Single paragraph, comma-separated tags
3. Include: shot type, character details, lighting, mood, composition, style
4. Use visual language, not narrative
5. Be specific and detailed
6. Match the emotional tone
7. Force a colored manga page layout: asymmetric comic panels, thick white gutters, one large action panel with one large clean readable character face, smaller reaction/detail inset panels should avoid tiny faces, dynamic diagonal panel borders, manga speed lines, impact strokes, clean ink outlines, cel shading, speech-box-safe empty areas, dramatic camera angles
8. Use the uploaded manga pages only as layout inspiration; do not copy their characters, text, faces, costumes, or exact panel art

EXAMPLE OUTPUT:
"Medium shot, cloaked figure standing at the edge of a cliff, stormy sky behind, mysterious atmosphere, dramatic rim lighting, volumetric fog, dark fantasy anime style, detailed face, cinematic composition, depth of field, 8k masterpiece"

Generate prompt now:"""

    response = _call_ollama(prompt, IMAGE_ENGINEER_MODEL, temperature=0.8, timeout=30)

    if not response:
        # Fallback to generic prompt
        log.warning("[image-engineer] No response, using fallback prompt")
        return (
            f"{visual_style or 'dark fantasy anime'}, {mood} atmosphere, colored manga page, "
            "asymmetric comic panels, thick white gutters, one large action panel with one large clean readable character face, smaller inset reaction panels without tiny faces, "
            "dynamic diagonal panel borders, manga speed lines, impact strokes, clean ink outlines, cel shading, "
            "speech-box-safe empty areas, dramatic camera angles, detailed"
        )

    # Clean up response
    response = response.strip()
    # Remove quotes if present
    if response.startswith('"') and response.endswith('"'):
        response = response[1:-1]
    # P4-12 fix: only strip a leading label when it matches a known label pattern.
    # The old code stripped everything before the first colon if a colon appeared
    # in the first 50 chars — this incorrectly truncated prompts that contain a
    # colon as part of the scene description (e.g. "Medium shot: cloaked figure...").
    # Only strip when the prefix is a known LLM output label keyword.
    _known_label = re.match(r"^(?:prompt|output|result|answer)\s*:", response, re.IGNORECASE)
    if _known_label:
        response = response[_known_label.end() :].strip()

    panel_anchor = (
        "colored manga page, asymmetric comic panel layout, thick white gutters, one large action panel, "
        "one large clean readable character face, smaller inset reaction panels without tiny faces, "
        "dynamic diagonal panel borders, manga speed lines, impact strokes, clean ink outlines, cel shading, "
        "speech-box-safe empty areas, symmetrical eyes, sharp facial features"
    )
    return f"{response}, {panel_anchor}"





def extract_world_state(text: str, config: dict) -> dict | None:
    """B3: Extract structured world state from a script using the 3B reviewer model.

    Returns a dict with keys: characters, facts, open_threads, resolved_threads.
    Returns None on failure (caller should fall back to regex extraction).

    This is Devanagari-aware because the LLM handles Unicode natively, unlike
    the regex extractor which only matches Latin/Devanagari initials.
    """
    if not text or not text.strip():
        return None

    try:
        config.get("ollama", {}).get("host", "http://localhost:11434")
        model = config.get("models", {}).get("reviewer", SCRIPT_REVIEWER_MODEL)
        timeout = int(config.get("ollama", {}).get("request_timeout", 60))

        prompt = f"""Analyze this story segment and extract structured world information.

SEGMENT:
{text[:1500]}

Extract and return ONLY valid JSON with these exact keys:
{{
  "characters": ["name1", "name2"],
  "facts": ["established fact 1", "established fact 2"],
  "open_threads": ["unresolved question or plot thread"],
  "resolved_threads": ["resolved plot thread"]
}}

Rules:
- characters: proper names of people/beings mentioned (include Devanagari names)
- facts: world rules, established truths, permanent states (max 3)
- open_threads: unanswered questions, unresolved conflicts (max 2)
- resolved_threads: conflicts/questions resolved in this segment (max 2)
- Output ONLY the JSON, no explanation"""

        response = _call_ollama(prompt, model, format_json=True, temperature=0.1, timeout=timeout)
        if not response:
            return None

        # Parse with extract_json
        try:
            result = extract_json(response)
            if isinstance(result, dict):
                # Validate and normalise
                return {
                    "characters": [str(c) for c in result.get("characters", []) if c],
                    "facts": [str(f) for f in result.get("facts", []) if f],
                    "open_threads": [
                        str(t) for t in result.get("open_threads", []) if t
                    ],
                    "resolved_threads": [
                        str(t) for t in result.get("resolved_threads", []) if t
                    ],
                }
        except Exception:
            pass
        log.warning(
            f"[B3] extract_world_state: could not parse JSON from response: {response[:100]}"
        )
        return None
    except Exception as e:
        log.warning(f"[B3] extract_world_state failed: {e}")
        return None
