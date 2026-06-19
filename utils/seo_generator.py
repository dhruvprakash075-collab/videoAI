"""seo_generator.py - Generates click-optimized SEO metadata for YouTube.

Phase 5: extended beyond title+tags to also produce description, hashtags,
chapter list, and language/source-path flags. The core LLM call still hits
the configured director model (default ``hermes-director``) for the creative
work (title, description); the deterministic fields (hashtags, chapters,
language, source_path flag) are derived locally without a second LLM call.

Source-path aware: when a :class:`SourceDocument` is passed, the prompt is
enriched with the document's title, language, and any front-matter / chapter
list, and the returned :data:`SEOMetadata` flags ``source_path=True`` so the
caller can branch.

LLM-failure contract: returns the deterministic fallback immediately on
breaker-open / network error / malformed JSON, with
``generation_succeeded=False``. The :func:`generate_seo_metadata` function
**never** raises — it is called from post-production after the video is
rendered and must not fail the upload.
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

from utils.utils import extract_json

log = logging.getLogger(__name__)


class SEOMetadata(TypedDict, total=False):
    """YouTube SEO metadata. All keys are optional; dict access is preserved
    for backward compat with the original 2-field shape.
    """

    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    chapters: list[dict]
    language: str
    source_path: bool
    generation_succeeded: bool


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
        "our",
        "their",
        "they",
        "we",
        "us",
        "i",
        "he",
        "she",
        "his",
        "her",
        "but",
        "not",
        "what",
        "when",
        "where",
        "who",
        "which",
        "why",
        "how",
        "all",
        "any",
        "some",
        "no",
        "yes",
    }
)


def _slugify_tag(word: str, max_len: int = 30) -> str:
    """Lowercase, strip punctuation, collapse to single token. No '#' prefix.

    Preserves Unicode letters AND combining marks (so Devanagari matras like
    ``े`` / ``ो`` survive). Strips everything that is not a letter, mark,
    digit, whitespace, or hyphen.
    """
    if not word:
        return ""
    s = word.lower().strip()
    try:
        import unicodedata

        s = "".join(
            c for c in s if unicodedata.category(c).startswith(("L", "M", "Nd", "Zs")) or c in "-_"
        )
    except Exception:
        s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "", s)
    return s[:max_len]


def _chapter_timecode(index: int, total: int) -> str:
    """Distribute the video into ``total`` equal chapters. ``index`` is 0-based.

    YouTube requires the first chapter at 0:00. Subsequent chapters are
    rounded to whole seconds. Returns a string like ``"3:45"`` or ``"1:02:30"``.
    """
    if total <= 0:
        return "0:00"
    seconds = round((index / total) * max(total * 60, 1))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_chapters(outline: list, max_count: int) -> list[dict]:
    """Build the chapter list from the story outline. Each entry has
    ``{"time": "0:00", "title": "..."}``. Capped at ``max_count`` items.
    """
    if not outline:
        return []
    n = min(len(outline), max_count)
    chapters = []
    for i in range(n):
        seg = outline[i] or {}
        title = seg.get("key_event") or seg.get("summary") or seg.get("title") or f"Part {i + 1}"
        chapters.append({"time": _chapter_timecode(i, n), "title": str(title)[:100]})
    return chapters


def _extract_tag_candidates(
    topic: str,
    outline: list,
    research_titles: list[str] | None = None,
) -> list[str]:
    """Build a deduplicated, slugified list of tag candidates.

    Source order (first appearance wins):
      1. Topic words (slugs).
      2. ``key_event`` / ``summary`` / ``title`` strings from each outline entry.
      3. Research item titles.
    """
    seen: set[str] = set()
    out: list[str] = []

    for raw in [
        topic,
        *(seg.get("key_event", "") for seg in outline),
        *(seg.get("summary", "") for seg in outline),
        *(seg.get("title", "") for seg in outline),
        *(research_titles or []),
    ]:
        if not raw:
            continue
        for word in re.split(r"[\s,;.!?\u0964]+", str(raw)):
            slug = _slugify_tag(word)
            if not slug or slug in _STOPWORDS or len(slug) < 3 or slug in seen:
                continue
            seen.add(slug)
            out.append(slug)
    return out


def _derive_hashtags(tags: list[str], count: int) -> list[str]:
    """Pick the top ``count`` tags, prefix with ``#``, and join as a space-
    separated string for the description body.
    """
    return [f"#{t}" for t in tags[:count] if t]


def _fallback_metadata(
    topic: str,
    outline: list,
    cfg: dict,
    language: str,
    source_path: bool,
) -> SEOMetadata:
    """Pure-local fallback. No LLM call."""
    seo_cfg = cfg.get("seo") or {}
    title_max = int(seo_cfg.get("title_max_chars", 100))
    desc_max = int(seo_cfg.get("description_max_chars", 5000))
    tag_n = int(seo_cfg.get("tags_count", 15))
    hash_n = int(seo_cfg.get("hashtags_count", 5))
    chap_max = int(seo_cfg.get("chapters_max", 50))

    tags = _extract_tag_candidates(topic, outline)[:tag_n]
    if not tags:
        tags = [_slugify_tag(topic) or "video", "AI", "Documentary"]
    title = (topic or "Video")[:title_max]
    description = (
        f"{topic}\n\n"
        f"This documentary explores {topic} in depth. "
        f"Watch the full video to learn everything you need to know.\n\n"
        f"Chapters:\n"
        + "\n".join(f"{c['time']} - {c['title']}" for c in _build_chapters(outline, chap_max))
    )[:desc_max]
    return SEOMetadata(
        title=title,
        description=description,
        tags=tags,
        hashtags=_derive_hashtags(tags, hash_n),
        chapters=_build_chapters(outline, chap_max),
        language=language,
        source_path=source_path,
        generation_succeeded=False,
    )


def _parse_seo_response(raw: str) -> dict | None:
    """Extract dict from JSON response.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = extract_json(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _build_prompt(
    topic: str,
    outline: list,
    cfg: dict,
    language: str,
    source_path: bool,
    research_titles: list[str] | None,
) -> str:
    seo_cfg = cfg.get("seo") or {}
    title_max = int(seo_cfg.get("title_max_chars", 100))
    desc_paras = int(seo_cfg.get("description_paragraphs", 2))
    tag_n = int(seo_cfg.get("tags_count", 15))

    events = [f"  Part {i + 1}: {seg.get('key_event', '')}" for i, seg in enumerate(outline) if seg]
    outline_block = "\n".join(events) if events else "  (no outline available)"

    research_block = ""
    if research_titles:
        research_block = "\nRelated research headlines:\n" + "\n".join(
            f"  - {t}" for t in research_titles[:5]
        )

    source_block = ""
    if source_path:
        source_block = f"\nThis video is adapted from a source document in language: {language}."

    return f"""You are an expert YouTube SEO strategist for a long-form documentary channel.

Generate a YouTube-optimized SEO package for the following video.

Topic: {topic}
Language: {language}
Source-path: {"yes" if source_path else "no"}

Outline:
{outline_block}
{research_block}
{source_block}

RULES:
1. TITLE: under {title_max} characters. Use a curiosity gap or surprising fact.
2. DESCRIPTION: {desc_paras} short paragraphs. Include a hook, a brief summary,
   and a closing CTA. No URLs, no hashtags in the description body.
3. TAGS: exactly {tag_n} lowercase keyword tags, no '#' prefix, no spaces.
4. Return ONLY valid JSON in this exact format — no markdown, no commentary:

{{
  "title": "<title>",
  "description": "<description>",
  "tags": ["tag1", "tag2", ..., "tag{tag_n}"]
}}
"""


def generate_seo_metadata(
    topic: str,
    outline: list,
    config: dict | None = None,
    *,
    source_document: object | None = None,
    research_items: list | None = None,
) -> SEOMetadata:
    """Generate YouTube SEO metadata (title, description, tags, hashtags,
    chapters, language, source_path flag).

    The LLM is called only for the creative fields (title, description, tags);
    hashtags, chapters, language, and source_path are derived locally. On
    LLM failure, returns the deterministic fallback with
    ``generation_succeeded=False`` and **never raises**.
    """
    if config is None:
        from config import load_config

        config = load_config()

    seo_cfg = config.get("seo") or {}
    if not seo_cfg.get("enabled", True):
        log.info("[SEO] Disabled in config; using fallback")
        return _fallback_metadata(topic, outline, config, language="en", source_path=False)

    source_path = source_document is not None
    language = getattr(source_document, "language", None) or "en"
    research_titles = [
        getattr(it, "title", "") for it in (research_items or []) if getattr(it, "title", "")
    ]

    if not outline and not research_titles and not source_path:
        log.info("[SEO] Empty outline + no research + no source; using fallback")
        return _fallback_metadata(
            topic, outline, config, language=language, source_path=source_path
        )

    prompt = _build_prompt(topic, outline, config, language, source_path, research_titles)
    model = (config.get("models") or {}).get("director", "hermes-director")

    try:
        from utils.crewai_breaker import guarded_ollama_call

        raw = guarded_ollama_call(
            prompt, model=model, format_json=True, temperature=0.7, num_predict=512
        )
        if raw:
            parsed = _parse_seo_response(raw)
            if parsed:
                title_max = int(seo_cfg.get("title_max_chars", 100))
                desc_max = int(seo_cfg.get("description_max_chars", 5000))
                tag_n = int(seo_cfg.get("tags_count", 15))
                hash_n = int(seo_cfg.get("hashtags_count", 5))
                chap_max = int(seo_cfg.get("chapters_max", 50))

                title = str(parsed.get("title", topic))[:title_max]
                description = str(parsed.get("description", ""))[:desc_max]
                tags_raw = parsed.get("tags", [])
                if not isinstance(tags_raw, list):
                    tags_raw = []
                tags = [_slugify_tag(str(t)) for t in tags_raw if _slugify_tag(str(t))][:tag_n]
                if not tags:
                    tags = _extract_tag_candidates(topic, outline, research_titles)[:tag_n]

                return SEOMetadata(
                    title=title,
                    description=description,
                    tags=tags,
                    hashtags=_derive_hashtags(tags, hash_n),
                    chapters=_build_chapters(outline, chap_max),
                    language=language,
                    source_path=source_path,
                    generation_succeeded=True,
                )
    except Exception as e:
        log.warning(f"[SEO] Metadata generation failed: {e}")

    log.info("[SEO] Using fallback metadata")
    return _fallback_metadata(topic, outline, config, language=language, source_path=source_path)
