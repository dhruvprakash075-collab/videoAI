"""story.py - StoryMixin: research, story generation/parsing, pacing helpers.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StoryMixin:
    """Story: research, invented/read stories, cliffhangers, compaction, pacing."""

    # Provided by the composing DirectorAgent (facade __init__ + sibling mixins)
    llm_config: Any
    _prompt: Any
    _call_ollama: Any
    _parse_json: Any

    def _research_cache_path(self, topic: str) -> "Path":
        """Path for cached research results."""

        cdir = (
            self.llm_config.get("cache_dir", "cache")
            if isinstance(self.llm_config, dict)
            else "cache"
        )

        cache_dir = Path(cdir)

        cache_dir.mkdir(parents=True, exist_ok=True)

        return cache_dir / f"research_{re.sub(r'[^a-z0-9_]', '_', topic.strip().lower())[:60]}.json"

    # ── Research & Analysis ──

    def research_story(self, topic: str) -> dict:
        """Research background context for ``topic`` via the consolidated researcher.

        Delegates to :func:`utils.researcher.research_topic`, which honors the
        ``research.*`` config block (sources, budget, per-source limit, RSS
        feeds, User-Agent). The returned ``list[ResearchItem]`` is adapted into
        the dict shape the Director's downstream phases expect:
        ``{topic, combined_summary, result_count, raw_results}``.
        """

        log.info(f"[DIRECTOR] Phase 1/5: Researching '{topic}'...")

        config = self.llm_config if isinstance(self.llm_config, dict) else {}

        try:
            from utils.researcher import research_topic
        except ImportError:
            log.warning("[DIRECTOR] researcher module not available, using empty research")
            return {
                "topic": topic,
                "combined_summary": topic,
                "result_count": 0,
                "raw_results": [],
            }

        try:
            items = research_topic(topic, config)
        except Exception as exc:  # research must never break the pipeline
            log.warning(f"[DIRECTOR] Research failed ({exc}); using empty research")
            return {
                "topic": topic,
                "combined_summary": topic,
                "result_count": 0,
                "raw_results": [],
            }

        raw_results = [
            {
                "source": item.source_type,
                "title": item.title,
                "summary": item.text,
                "url": item.url,
                "relevance_score": item.relevance_score,
            }
            for item in items
        ]

        combined_parts = []
        for entry in raw_results:
            summary = (entry["summary"] or "").strip()
            if not summary:
                continue
            label = (entry["source"] or "source").capitalize()
            combined_parts.append(f"[{label}: {entry['title']}] {summary}")

        combined_summary = "\n\n".join(combined_parts)
        if len(combined_summary) > 4000:
            combined_summary = combined_summary[:4000] + "..."
        if not combined_summary:
            combined_summary = topic

        return {
            "topic": topic,
            "combined_summary": combined_summary,
            "result_count": len(raw_results),
            "raw_results": raw_results,
        }

    def invent_story(self, topic: str, user_notes: str, force_refresh: bool = False) -> str:
        """Generate an original story from scratch.

        A5: Caches the invented story to cache/story_{topic_hash}.json so the same
        topic doesn't pay the LLM cost twice. Pass force_refresh=True or use
        --no-resume to bypass the cache.
        """

        # A5: check cache first
        _cache_enabled = False
        try:
            _cfg = self.llm_config if isinstance(self.llm_config, dict) else {}
            _cache_enabled = _cfg.get("cache", {}).get("cache_invented_story", True)
        except Exception as exc:
            log.debug(f"[DIRECTOR] Invented-story cache config unavailable: {exc}")

        if _cache_enabled and not force_refresh:
            _topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
            _cache_dir = Path(
                self.llm_config.get("cache_dir", "cache")
                if isinstance(self.llm_config, dict)
                else "cache"
            )
            _cache_dir.mkdir(parents=True, exist_ok=True)
            _cache_path: Path | None = _cache_dir / f"story_{_topic_hash}.json"
            try:
                assert _cache_path is not None
                _cache_path.resolve().relative_to(_cache_dir.resolve())
            except ValueError:
                log.warning(f"[DIRECTOR] A5: cache path escapes cache dir: {_cache_path}")
                _cache_path = None
            if _cache_path and _cache_path.exists():
                try:
                    _cached = json.loads(_cache_path.read_text(encoding="utf-8"))
                    _story = _cached.get("story", "")
                    if _story:
                        log.info(
                            f"[DIRECTOR] A5: story cache hit for '{topic[:40]}' ({len(_story.split())} words)"
                        )
                        return _story
                except Exception as _ce:
                    log.debug(f"[DIRECTOR] A5: cache read failed ({_ce}), regenerating")

        prompt = self._prompt("invent_story", topic=topic, notes=user_notes) or (
            f"Create a short dramatic story about: {topic}. {user_notes}\n"
            f"Length: ~500 words. Include 2-3 characters and a clear arc."
        )

        res = self._call_ollama(prompt)

        log.info(f"[DIRECTOR] Story invented: {len(res.split())} words")

        # A5: write to cache
        if _cache_enabled and res:
            try:
                _topic_hash = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:12]
                _cache_dir = Path(
                    self.llm_config.get("cache_dir", "cache")
                    if isinstance(self.llm_config, dict)
                    else "cache"
                )
                _cache_dir.mkdir(parents=True, exist_ok=True)
                _cache_path = _cache_dir / f"story_{_topic_hash}.json"
                try:
                    _cache_path.resolve().relative_to(_cache_dir.resolve())
                except ValueError:
                    log.warning(f"[DIRECTOR] A5: cache write path escapes cache dir: {_cache_path}")
                    raise
                _cache_path.write_text(
                    json.dumps({"topic": topic, "story": res}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                log.info(f"[DIRECTOR] A5: story cached to {_cache_path.name}")
            except Exception as _cwe:
                log.debug(f"[DIRECTOR] A5: cache write failed ({_cwe})")

        return res

    def read_story(self, full_script: str) -> dict[str, Any]:
        """Parse a full story script into structured segments."""
        if not full_script:
            return {"segments": [], "total_words": 0}

        # Try to find splitters like [Segment 1], Segment 1:, Part 1:, ## Part 1, etc.
        pattern = r"(?:\[Segment\s+\d+\]|Segment\s+\d+:|\[Part\s+\d+\]|Part\s+\d+:|##\s+(?:Part|Segment)\s+\d+)"
        splits = re.split(pattern, full_script, flags=re.IGNORECASE)
        headers = re.findall(pattern, full_script, flags=re.IGNORECASE)

        segments = []
        if len(splits) > 1:
            # The first part before any header might be empty or preamble
            splits[0].strip()
            for idx, part in enumerate(splits[1:]):
                header = headers[idx] if idx < len(headers) else f"Part {idx + 1}"
                text = part.strip()
                if text:
                    segments.append(
                        {"header": header, "text": text, "word_count": len(text.split())}
                    )
        else:
            # Split by double newlines (paragraphs)
            paragraphs = [p.strip() for p in full_script.split("\n\n") if p.strip()]
            current_seg: list[Any] = []
            current_word_count = 0
            seg_idx = 1
            for p in paragraphs:
                p_words = len(p.split())
                if current_word_count + p_words > 250 and current_seg:
                    text = "\n\n".join(current_seg)
                    segments.append(
                        {
                            "header": f"Segment {seg_idx}",
                            "text": text,
                            "word_count": current_word_count,
                        }
                    )
                    seg_idx += 1
                    current_seg = [p]
                    current_word_count = p_words
                else:
                    current_seg.append(p)
                    current_word_count += p_words
            if current_seg:
                text = "\n\n".join(current_seg)
                segments.append(
                    {"header": f"Segment {seg_idx}", "text": text, "word_count": current_word_count}
                )

        # Fallback if no segments resolved
        if not segments:
            segments = [
                {"header": "Segment 1", "text": full_script, "word_count": len(full_script.split())}
            ]

        total_words = sum(int(s["word_count"]) for s in segments)

        # Estimate last estimated minutes based on segment count
        self._last_estimated_minutes = len(segments)

        return {"segments": segments, "total_words": total_words, "theme": "Untitled Story"}

    def define_pacing_and_length(self, vision_doc: dict) -> int:
        """Determine pacing and target length from vision doc."""

        return self._last_estimated_minutes

    def suggest_cliffhangers(self, content: str, current_minutes: int) -> list:
        """Suggest 2–3 high-note end points for a cliffhanger-style video cut.

        Returns a list of dicts: [{point: float(0-100), outcome: str, reason: str}]
        One pre-production LLM call, only when the user chooses cliffhanger mode.
        """
        if not content or len(content) < 200:
            log.warning("[DIRECTOR] suggest_cliffhangers: content too short, returning defaults")
            return [
                {
                    "point": 50,
                    "outcome": "Story reaches its midpoint climax",
                    "reason": "Natural midpoint",
                },
                {
                    "point": 75,
                    "outcome": "Story reaches a dramatic turning point",
                    "reason": "Three-quarter climax",
                },
            ]

        prompt = (
            f"You are a creative director analyzing a story for a video production.\n"
            f"The full video would be approximately {current_minutes} minutes.\n"
            f"Identify 2-3 dramatic high-note moments in the story where the video could end "
            f"on a cliffhanger — leaving the audience wanting more.\n\n"
            f"Story excerpt (first 3000 chars):\n{content[:3000]}\n\n"
            f"For each cliffhanger point output JSON:\n"
            f'{{"cliffhangers": [{{"point": <0-100 percent through story>, '
            f'"outcome": "<one sentence describing the dramatic moment>", '
            f'"reason": "<why this is a good cliffhanger>"}}]}}\n'
            f"Output ONLY the JSON. Provide exactly 2-3 options."
        )

        try:
            raw = self._call_ollama(prompt, format_json=True)
            parsed = self._parse_json(raw, {"cliffhangers": []})
            cliffs = parsed.get("cliffhangers", [])
            # Validate and clean
            result = []
            for c in cliffs:
                if isinstance(c, dict) and "point" in c and "outcome" in c:
                    point = max(10, min(95, float(c["point"])))
                    result.append(
                        {
                            "point": point,
                            "outcome": str(c.get("outcome", "Dramatic moment"))[:120],
                            "reason": str(c.get("reason", ""))[:120],
                        }
                    )
            if len(result) >= 2:
                log.info(f"[DIRECTOR] Cliffhanger options: {len(result)} points")
                return sorted(result, key=lambda x: x["point"])
        except Exception as e:
            log.warning(f"[DIRECTOR] suggest_cliffhangers LLM call failed: {e}")

        # Fallback
        return [
            {
                "point": 50,
                "outcome": "Story reaches its midpoint climax",
                "reason": "Natural midpoint",
            },
            {
                "point": 75,
                "outcome": "Story reaches a dramatic turning point",
                "reason": "Three-quarter climax",
            },
        ]

    def compact_story(self, content: str, target_minutes: int, original_minutes: int) -> str:
        """Condense story text to fit a target video duration.

        Uses the Director model to intelligently compress the narrative while
        preserving key characters, plot points, and emotional beats.
        One pre-production LLM call, only when the user chooses compact mode.
        """
        if not content or len(content) < 100:
            return content

        if target_minutes >= original_minutes:
            log.info("[DIRECTOR] compact_story: target >= original, no compaction needed")
            return content

        ratio = target_minutes / max(1, original_minutes)
        target_words = int(len(content.split()) * ratio)

        log.info(
            f"[DIRECTOR] Compacting story: {original_minutes}min → {target_minutes}min "
            f"(ratio={ratio:.2f}, target ~{target_words} words)"
        )

        prompt = (
            f"You are a skilled story editor condensing a narrative for video production.\n"
            f"The original story is approximately {original_minutes} minutes of video.\n"
            f"Condense it to fit {target_minutes} minutes while:\n"
            f"  - Preserving all main characters and their key traits\n"
            f"  - Keeping the core plot arc and emotional journey\n"
            f"  - Maintaining the most dramatic and impactful moments\n"
            f"  - Removing subplots, repetition, and minor details\n"
            f"  - Targeting approximately {target_words} words\n\n"
            f"ORIGINAL STORY:\n{content[:8000]}\n\n"
            f"Output ONLY the condensed story text, no commentary or labels."
        )

        try:
            compacted = self._call_ollama(prompt, model_type="director")
            if compacted and len(compacted.split()) > 50:
                log.info(
                    f"[DIRECTOR] Story compacted: {len(content.split())} → "
                    f"{len(compacted.split())} words"
                )
                return compacted
            log.warning("[DIRECTOR] compact_story returned empty/short result — using original")
            return content
        except Exception as e:
            log.warning(f"[DIRECTOR] compact_story LLM call failed: {e} — using original")
            return content
