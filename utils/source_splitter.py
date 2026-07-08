"""source_splitter.py - Split a SourceDocument into N per-segment chunks.

Three strategies (selected via ``source.split_strategy`` in config):

  * ``by_chapter``     - For .md / .docx only. Splits on H1 / H2 boundaries
                         (markdown lines starting with `# ` or `## `, or
                         ``Heading 1`` / ``Heading 2`` styles in DOCX).
                         Falls back to ``by_word_count`` for .txt / .pdf / URL /
                         paste when no headings are detected.
  * ``by_word_count``  - Sentence-aware split. Groups sentences until ~target
                         words (default: ``script.words_per_segment`` = 100),
                         then rebalances to match ``n_segments`` exactly.
  * ``by_llm``         - Calls the configured writer model once with a
                         "story structure analyst" prompt. Receives a JSON
                         list of {text, b_roll_hint, key_event} objects.
                         Falls back to ``by_word_count`` if the LLM call
                         fails or returns a malformed payload.

Each chunk is a :class:`SegmentChunk`:

  - ``text``         - the verbatim source excerpt (or LLM-curated excerpt)
  - ``b_roll_hint``  - 1-line visual cue for the SD prompt enricher
  - ``key_event``    - 1-line summary used in the story plan / continuity log
  - ``index``        - 0..n_segments-1
  - ``source_chapter`` - heading text (only for ``by_chapter``; "" otherwise)

Pure dispatcher + small helpers. GPU / LLM only inside ``_split_by_llm``,
which is mockable via :func:`split_source` injection (see tests).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from utils.utils import extract_json

log = logging.getLogger(__name__)


SUPPORTED_STRATEGIES = ("by_chapter", "by_word_count", "by_llm")

_SENTENCE_RE = re.compile(r"(?<=[.!?\u0964])\s+|\n+")
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$", re.MULTILINE)


class SourceSplitterError(Exception):
    """Raised when a source cannot be split (bad strategy, empty input, etc.)."""


@dataclass
class SegmentChunk:
    """One per-segment slice of the source material."""

    text: str
    b_roll_hint: str = ""
    key_event: str = ""
    index: int = 0
    source_chapter: str = ""


def _split_sentences(text: str) -> list[str]:
    """Devanagari-aware sentence splitter.

    Splits on whitespace runs following ``.`` / ``!`` / ``?`` / ``\u0964``
    (Devanagari danda) and on standalone newlines. Preserves the original
    substrings verbatim — no punctuation is added or removed.
    """
    if not text.strip():
        return []
    parts = _SENTENCE_RE.split(text.strip())
    return [p for p in parts if p.strip()]


def _parse_md_headings(text: str) -> list[tuple[int, str]]:
    """Return [(level, title), ...] for # / ## headings in markdown text.

    Level is 1 for H1, 2 for H2. Empty list if no headings found.
    """
    return [(len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(text)]


def _split_by_chapter(source, n: int) -> list[SegmentChunk]:
    """Split on H1/H2 headings. MD: parse ``#`` lines. DOCX: use metadata.

    Returns an empty list if no headings are detected (caller should fall back
    to ``by_word_count``).
    """
    if source.source_type == "md":
        headings = _parse_md_headings(source.text)
    elif source.source_type == "docx":
        raw = source.metadata.get("headings", [])
        headings = []
        for entry in raw:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            style, title = entry[0], entry[1]
            level = 1 if "1" in str(style) else (2 if "2" in str(style) else 0)
            if level and title:
                headings.append((level, str(title)))
    else:
        return []

    if not headings:
        return []

    sections: list[tuple[str, str]] = []
    text = source.text
    boundaries: list[tuple[int, int, str]] = []
    for m in _HEADING_RE.finditer(text):
        if len(m.group(1)) <= 2:
            boundaries.append((m.start(), m.end(), m.group(2)))

    if not boundaries and source.source_type == "docx":
        body_start = 0
        for i, (_level, title) in enumerate(headings):
            start = body_start
            end = (
                len(text)
                if i == len(headings) - 1
                else boundaries[i + 1][0]
                if i + 1 < len(boundaries)
                else len(text)
            )
            section_text = text[start:end].strip()
            if section_text:
                sections.append((title, section_text))
            body_start = end
    else:
        for i, (_start, end, title) in enumerate(boundaries):
            section_end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            section_text = text[end:section_end].strip()
            if section_text:
                sections.append((title, section_text))

    if not sections:
        return []

    chunks = [SegmentChunk(text=body, source_chapter=title) for title, body in sections]
    return _rebalance(chunks, n)


def _split_by_word_count(text: str, n: int, target_words: int) -> list[SegmentChunk]:
    """Group sentences into chunks of ~target_words.

    Returns the raw grouping (the dispatcher calls :func:`_rebalance` to match
    ``n`` exactly). The ``n`` parameter is accepted for symmetry with the
    chapter/llm strategies and is currently unused.
    """
    sentences = [
        s
        for s in _split_sentences(text)
        if not re.match(r"^\s*#{1,6}\s+\S", s)
    ]
    if not sentences:
        return []

    raw: list[SegmentChunk] = []
    buf: list[str] = []
    buf_words = 0
    for sent in sentences:
        sw = len(sent.split())
        if buf and (buf_words + sw) > target_words and buf_words >= target_words * 0.5:
            raw.append(SegmentChunk(text=" ".join(buf).strip()))
            buf = [sent]
            buf_words = sw
        else:
            buf.append(sent)
            buf_words += sw
    if buf:
        raw.append(SegmentChunk(text=" ".join(buf).strip()))

    return raw


def _rebalance(chunks: list[SegmentChunk], n: int) -> list[SegmentChunk]:
    """Merge or split chunks so the returned list has exactly ``n`` entries.

    Merging concatenates ``text`` with two newlines. Splitting picks the
    midpoint sentence boundary of the largest chunk.
    """
    if n <= 0:
        return []
    if not chunks:
        return [SegmentChunk(text="")] * n
    if len(chunks) == n:
        return _index(chunks)

    while len(chunks) > n:
        i = min(
            range(len(chunks) - 1),
            key=lambda k: len(chunks[k].text.split()) + len(chunks[k + 1].text.split()),
        )
        a, b = chunks[i], chunks[i + 1]
        merged = SegmentChunk(
            text=(a.text + "\n\n" + b.text).strip(),
            b_roll_hint=a.b_roll_hint or b.b_roll_hint,
            key_event=a.key_event or b.key_event,
            source_chapter=a.source_chapter or b.source_chapter,
        )
        chunks = [*chunks[:i], merged, *chunks[i + 2 :]]

    while len(chunks) < n:
        idx = max(range(len(chunks)), key=lambda k: len(chunks[k].text.split()))
        big = chunks[idx]
        sentences = _split_sentences(big.text)
        if len(sentences) < 2:
            break
        mid = len(sentences) // 2
        left = SegmentChunk(
            text=" ".join(sentences[:mid]).strip(),
            b_roll_hint=big.b_roll_hint,
            key_event=big.key_event,
            source_chapter=big.source_chapter,
        )
        right = SegmentChunk(
            text=" ".join(sentences[mid:]).strip(),
            b_roll_hint=big.b_roll_hint,
            key_event=big.key_event,
            source_chapter=big.source_chapter,
        )
        chunks = [*chunks[:idx], left, right, *chunks[idx + 1 :]]

    if len(chunks) < n:
        chunks = chunks + [SegmentChunk(text="")] * (n - len(chunks))

    return _index(chunks)


def _index(chunks: list[SegmentChunk]) -> list[SegmentChunk]:
    for i, c in enumerate(chunks):
        c.index = i
    return chunks


def _parse_llm_chunks(raw: str, expected: int) -> list[dict] | None:
    """Extract list from JSON response.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = extract_json(raw)
        if isinstance(data, list):
            return data[:expected] if len(data) >= expected else None
    except Exception as exc:
        log.debug(f"Source split JSON parse failed: {exc}")
    return None


def _split_by_llm(source, n: int, config: dict) -> list[SegmentChunk]:
    """Call the writer model to chunk the source. Falls back to by_word_count
    on any failure (returns [] to signal the fallback to the dispatcher).
    """
    if not source.text.strip():
        return []
    try:
        from utils.crewai_breaker import guarded_ollama_call
    except Exception:
        return []

    model = (config.get("models") or {}).get("writer", "")
    if not model:
        return []

    prompt = (
        "You are a story structure analyst. Split the following source text into "
        f"exactly {n} segments for a long-form video script.\n\n"
        "For each segment return a JSON object with:\n"
        '  - "text"        : the verbatim excerpt (or lightly trimmed) from the source for that segment\n'
        '  - "b_roll_hint" : a single short visual cue (e.g. "candle on a dark desk") for B-roll\n'
        '  - "key_event"   : a one-sentence summary of what happens in this segment\n\n'
        f"Return ONLY a JSON array of {n} objects. No markdown, no commentary.\n\n"
        f"SOURCE:\n{source.text}\n"
    )
    raw = guarded_ollama_call(
        prompt, model=model, format_json=True, temperature=0.2, num_predict=2048
    )
    if not raw:
        return []
    parsed = _parse_llm_chunks(raw, n)
    if not parsed:
        return []

    chunks: list[SegmentChunk] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            return []
        chunks.append(
            SegmentChunk(
                text=str(item.get("text", "")).strip(),
                b_roll_hint=str(item.get("b_roll_hint", "")).strip(),
                key_event=str(item.get("key_event", "")).strip(),
                index=i,
            )
        )
    return _rebalance(chunks, n) if len(chunks) != n else _index(chunks)


def split_source(source, n_segments: int, config: dict) -> list[SegmentChunk]:
    """Dispatcher. ``source`` is a :class:`SourceDocument`.

    Reads ``source.split_strategy`` (default ``by_word_count``) and the per-
    segment target from ``script.words_per_segment`` (default 100).
    """
    if n_segments <= 0:
        raise SourceSplitterError(f"n_segments must be > 0, got {n_segments}")
    if not source.text or not source.text.strip():
        return [SegmentChunk(text="", index=0) for _ in range(n_segments)]

    strategy = (config.get("source") or {}).get("split_strategy", "by_word_count")
    if strategy not in SUPPORTED_STRATEGIES:
        raise SourceSplitterError(
            f"Unknown split_strategy: {strategy!r}. Supported: {SUPPORTED_STRATEGIES}"
        )
    target_words = int(((config.get("script") or {}).get("words_per_segment")) or 100)

    chunks: list[SegmentChunk] = []

    if strategy == "by_chapter":
        chunks = _split_by_chapter(source, n_segments)
        if not chunks:
            log.info(
                "[source_splitter] No headings in %s source; falling back to by_word_count",
                source.source_type,
            )
            chunks = _split_by_word_count(source.text, n_segments, target_words)
    elif strategy == "by_word_count":
        chunks = _split_by_word_count(source.text, n_segments, target_words)
    elif strategy == "by_llm":
        chunks = _split_by_llm(source, n_segments, config)
        if not chunks:
            log.warning("[source_splitter] LLM split failed; falling back to by_word_count")
            chunks = _split_by_word_count(source.text, n_segments, target_words)

    if not chunks:
        return [SegmentChunk(text="", index=i) for i in range(n_segments)]

    return _rebalance(chunks, n_segments) if len(chunks) != n_segments else _index(chunks)
