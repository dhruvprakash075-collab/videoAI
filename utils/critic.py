"""critic.py - Self-critique for writer scripts using prompt-swap on the same model.

Replaces the auto-approve stub in :func:`core.segment_runner.critic_node`. The
critic uses the same ``models.writer`` model (e.g. ``zephyr-writer``) but with
a different system prompt — no extra model swap, no extra VRAM hit.

5-dimension rubric (20 pts each = 100 total):

  * **hook**             - Does the first sentence grab attention?
  * **emotional_arc**    - Does the emotional tone move through the segment?
  * **pacing**           - Are sentence lengths varied? Is there momentum?
  * **retention**        - Will a casual viewer stay engaged to the end?
  * **tts_friendliness** - Will the TTS engine (OmniVoice Hindi) pronounce it
                            cleanly? No abbreviations, URLs, stage directions,
                            or mixed scripts?

Threshold (default 60) and max rewrites (default 2) are read from the
``critic`` config section. On LLM failure the critic auto-approves with a
warning (graceful degradation, mirrors Phase 0.5.5b).

The critic ONLY scores. Rewriting is the writer's job — when the score is
below threshold, the graph routes back to ``write_script_node`` which
incorporates :attr:`SegmentState.critic_feedback` into its prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from utils.utils import extract_json

log = logging.getLogger(__name__)


DIMENSIONS = ("hook", "emotional_arc", "pacing", "retention", "tts_friendliness")
DIMENSION_MAX = 20
TOTAL_MAX = DIMENSION_MAX * len(DIMENSIONS)


@dataclass
class CriticScore:
    """Per-dimension scores (0-20 each) + free-form feedback."""

    hook: int = 0
    emotional_arc: int = 0
    pacing: int = 0
    retention: int = 0
    tts_friendliness: int = 0
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.hook + self.emotional_arc + self.pacing + self.retention + self.tts_friendliness

    @property
    def is_empty(self) -> bool:
        """True when the LLM call failed and the score is a default-zero."""
        return self.total == 0 and not self.issues and not self.suggestions

    def to_dict(self) -> dict:
        return {
            "hook": self.hook,
            "emotional_arc": self.emotional_arc,
            "pacing": self.pacing,
            "retention": self.retention,
            "tts_friendliness": self.tts_friendliness,
            "total": self.total,
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
        }


CRITIC_PROMPT = """You are an expert script critic for a long-form Hindi documentary video.

Score the following script on FIVE dimensions (0-20 points each, total 0-100):

1. **hook** (0-20): Does the first sentence grab attention? Does it pose a
   question, drop a surprising fact, or create a curiosity gap?
2. **emotional_arc** (0-20): Does the emotional tone move through the
   segment? (e.g. neutral -> tense -> revelatory). Or is it flat?
3. **pacing** (0-20): Are sentence lengths varied? Any lulls? Is there
   momentum building toward a climax?
4. **retention** (0-20): Will a casual viewer stay engaged to the end? Are
   there micro-hooks every 2-3 sentences?
5. **tts_friendliness** (0-20): Will the TTS engine (OmniVoice Hindi)
   pronounce it cleanly? No abbreviations, no URLs, no parenthetical stage
   directions, no mathematical symbols, no foreign-script mixed with
   Devanagari.

Return ONLY valid JSON in this exact format - no other text:
{{
  "hook": <int 0-20>,
  "emotional_arc": <int 0-20>,
  "pacing": <int 0-20>,
  "retention": <int 0-20>,
  "tts_friendliness": <int 0-20>,
  "issues": ["<short string describing a problem>", ...],
  "suggestions": ["<short string describing a fix>", ...]
}}

SCRIPT TO CRITIQUE:
{script}

JSON:"""


REWRITE_PROMPT = """You are an expert script rewriter for a Hindi documentary video.

The following script was scored {total}/100 (threshold: {threshold}).
Issues found: {issues}
Suggestions: {suggestions}

Rewrite the script to address the LOWEST-SCORING dimensions while keeping
the core content. Maintain the same word count (within +/- 10%) and the
same language (do NOT translate to English if the original is Hindi).

Rules:
- Preserve the original first sentence's intent (the hook).
- Address pacing issues by varying sentence length.
- Address tts-friendliness by removing any non-pronounceable symbols.
- Keep the total narration in the same language as the input.

Return ONLY the rewritten script. No JSON, no markdown, no commentary.

ORIGINAL SCRIPT:
{script}

REWRITTEN SCRIPT:"""


def _clamp_dim(v, max_val: int = DIMENSION_MAX) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, min(max_val, v))


def _score_from_dict(data: dict) -> CriticScore:
    issues = data.get("issues", [])
    suggestions = data.get("suggestions", [])
    if not isinstance(issues, list):
        issues = []
    if not isinstance(suggestions, list):
        suggestions = []
    return CriticScore(
        hook=_clamp_dim(data.get("hook")),
        emotional_arc=_clamp_dim(data.get("emotional_arc")),
        pacing=_clamp_dim(data.get("pacing")),
        retention=_clamp_dim(data.get("retention")),
        tts_friendliness=_clamp_dim(data.get("tts_friendliness")),
        issues=[str(i) for i in issues if str(i).strip()],
        suggestions=[str(s) for s in suggestions if str(s).strip()],
    )


def parse_critic_json(raw: str) -> CriticScore | None:
    """Extract CriticScore from JSON.

    Returns ``None`` on any failure. The returned :class:`CriticScore` has
    individual dimensions clamped to ``[0, 20]``.
    """
    if not raw or not raw.strip():
        return None

    try:
        data = extract_json(raw)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            return _score_from_dict(data)
    except Exception as exc:
        log.debug(f"Critic score parse failed: {exc}")

    return None


def is_approved(score: CriticScore, threshold: int) -> bool:
    return score.total >= threshold


def _critic_config(config: dict) -> tuple[int, int]:
    cfg = config.get("critic") or {}
    return int(cfg.get("threshold", 60)), int(cfg.get("max_rewrites", 2))


def score_script(script: str, config: dict) -> CriticScore | None:
    """Single critic LLM call. Returns the parsed :class:`CriticScore` or
    ``None`` if the LLM call failed (breaker open, network error, etc.).
    """
    try:
        from utils.crewai_breaker import guarded_ollama_call
    except ImportError:
        log.debug("[critic] crewai_breaker not importable; cannot score")
        return None

    model = (config.get("models") or {}).get("writer", "zephyr-writer")
    prompt = CRITIC_PROMPT.format(script=script)

    raw = guarded_ollama_call(
        prompt, model=model, format_json=True, temperature=0.2, num_predict=512
    )
    if not raw:
        return None
    return parse_critic_json(raw)


def rewrite_script(script: str, score: CriticScore, threshold: int, config: dict) -> str | None:
    """One-shot rewrite using the same writer model with :data:`REWRITE_PROMPT`.

    Returns the new script (stripped) or ``None`` on failure.
    """
    try:
        from utils.crewai_breaker import guarded_ollama_call
    except ImportError:
        return None

    model = (config.get("models") or {}).get("writer", "zephyr-writer")
    prompt = REWRITE_PROMPT.format(
        total=score.total,
        threshold=threshold,
        issues="; ".join(score.issues) or "(none specific)",
        suggestions="; ".join(score.suggestions) or "(improve weakest dimensions)",
        script=script,
    )
    raw = guarded_ollama_call(
        prompt, model=model, format_json=False, temperature=0.5, num_predict=1024
    )
    if not raw:
        return None
    return raw.strip()


def critique_and_rewrite(script: str, config: dict) -> tuple[str, CriticScore, int]:
    """Convenience: score + up to ``max_rewrites`` self-rewrites, in a loop.

    Returns ``(best_script, best_score, attempts_used)``. Used by callers that
    want a one-shot retry (e.g. CLI tools); the LangGraph integration uses
    :func:`score_script` directly and relies on the graph routing for retries.
    """
    threshold, max_rewrites = _critic_config(config)
    score = score_script(script, config)
    if score is None:
        return (script, CriticScore(), 0)
    if is_approved(score, threshold):
        return (script, score, 0)

    best_script = script
    best_score = score
    attempts = 0

    for attempt in range(1, max_rewrites + 1):
        new_script = rewrite_script(best_script, best_score, threshold, config)
        if not new_script:
            break
        new_score = score_script(new_script, config)
        if new_score is None:
            break
        attempts = attempt
        if new_score.total > best_score.total:
            best_script = new_script
            best_score = new_score
        if is_approved(best_score, threshold):
            return (best_script, best_score, attempt)

    return (best_script, best_score, attempts)
