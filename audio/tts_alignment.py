"""tts_alignment.py - Generate word-level timestamps for TTS output.

Runs faster-whisper (CPU int8) on a WAV file and writes word timestamps to
"{wav_path}.words.json" as:
  [{"word": str, "start": float, "end": float}, ...]

Called from TTS workers so the renderer never needs to run Whisper as a
fallback for word timing.

Optional tail-trim (``trim_tails=True``): TTS models drawl the last syllable
at segment boundaries, and whisper transcribes those drawls as standalone
ग-forms (गगगग, गगे, गई). The drawls are cut out of the WAV before timestamps
are written. See ``_trim_ranges`` for the heuristic's scope.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_alignment_model = None
_alignment_model_name = None
_alignment_lock = threading.Lock()

# Devanagari combining marks + the independent vowels whisper writes instead
# (ए U+090F for े) so word skeletons keep only consonants.
_MATRAS = {
    0x0901, 0x0902, 0x0903, 0x093C,
    *range(0x0904, 0x0915),
    *range(0x093E, 0x094D + 1),
    *range(0x0951, 0x0957 + 1),
    0x0962, 0x0963,
    0x200C, 0x200D,
}
_PUNCT = set("।.!?,;:\"'’‘“”–—…()")
# Drawl tokens TTS emits at segment edges (गगगग, गगे, गई).
_DRAWL_RE = re.compile(r"^ग[ेीईं]?$|^ग{2,}[ेीईं]?$")

# Weak models transcribe drawls as real words (masking them); trim would see
# nothing to cut. Only models >= small hear the ग-forms (gated by name in
# align_audio: "base"/"tiny" in model_name disables trimming).

def _norm(t: str) -> str:
    return "".join(ch for ch in t if ch not in _PUNCT).strip()


def _skeleton(t: str) -> str:
    return "".join(ch for ch in t if ord(ch) not in _MATRAS)


def _dist(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _match_threshold(skel_len: int) -> int:
    if skel_len <= 1:
        return 0
    return 1 if skel_len == 2 else 2


def _raw_dist(a: str, b: str) -> int:
    """Compare raw text when either side is tiny (<=2 code points), keeping
    matra distinctions whisper preserves (कि vs के); skeleton otherwise."""
    a, b = a.strip(), b.strip()
    if len(a) <= 2 or len(b) <= 2:
        return _dist(a, b)
    return _dist(_skeleton(a), _skeleton(b))


def _is_drawl(word: str) -> bool:
    return bool(_DRAWL_RE.match(_norm(word)))


def _is_junk(start: float, end: float) -> bool:
    return end - start < 0.01


def _align_words(words: list[dict], reference_text: str, window: int = 12) -> dict:
    """Greedy first-match alignment of whisper words against the true text.

    Returns {word_idx: edit_distance}. Zero-duration words are whisper junk
    and never match. The monotonic pointer absorbs whisper's dropped words;
    the raw-vs-skeleton compare keeps short-token ambiguity (कि/के/को) from
    matching each other.
    """
    ref_tokens = [_norm(t) for t in reference_text.split() if _norm(t)]
    if not ref_tokens:
        return {}
    marks: dict[int, int] = {}
    j = 0
    for tok in ref_tokens:
        th = _match_threshold(len(_skeleton(tok)))
        best = None
        for k in range(j, min(j + window, len(words))):
            if _is_junk(words[k]["start"], words[k]["end"]):
                continue
            d = _raw_dist(tok, _norm(words[k]["word"]))
            if d <= th:
                best = (k, d)
                break
        if best is not None:
            marks[best[0]] = best[1]
            j = best[0] + 1
    return marks


def _trim_ranges(words: list[dict], segments: list[tuple[int, int]], marks: dict) -> list:
    """Compute WAV ranges to cut: leading/trailing runs of cuttable words per
    whisper segment. A word is cuttable iff it is zero-duration junk, or
    drawl-shaped AND not aligned to the true text at distance 0 (a real गई
    aligns exactly; a drawl गगे does not). Mid-segment drawls and unaligned
    real-sounding words (whisper mishears) are kept.

    ponytail: only ग-forms are recognized as drawls — the observed TTS
    artifact. Other geminated consonants (कक, दद) would need adding to
    _DRAWL_RE; the alignment check keeps false cuts unlikely either way.
    """
    if not words or not segments:
        return []
    ranges: list[tuple[float, float]] = []

    def _cuttable(idx: int) -> bool:
        w = words[idx]
        if _is_junk(w["start"], w["end"]):
            return True
        if not _is_drawl(w["word"]):
            return False
        return marks.get(idx) != 0  # unaligned or false-aligned => artifact

    for seg_start, seg_end in segments:
        seg_words = words[seg_start:seg_end]
        if not seg_words:
            continue
        lo = 0
        while lo < len(seg_words) and _cuttable(seg_start + lo):
            lo += 1
        hi = len(seg_words)
        while hi > lo and _cuttable(seg_start + hi - 1):
            hi -= 1
        if lo == hi:
            continue  # whole segment cuttable -> keep all (conservative)
        if lo > 0:
            ranges.append((seg_words[0]["start"], seg_words[lo]["start"]))
        if hi < len(seg_words):
            ranges.append((seg_words[hi]["start"], seg_words[-1]["end"]))

    ranges.sort()
    merged: list[tuple[float, float]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _cut_wav(wav_path: Path, ranges: list) -> bool:
    """Rewrite the WAV removing the given (non-overlapping) ranges."""
    try:
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(str(wav_path), dtype="float32")
        n = len(data)
        bounds = [0.0] + [t for s, e in ranges for t in (s, e)] + [n / sr]
        kept = [
            data[int(bounds[i] * sr):int(bounds[i + 1] * sr)]
            for i in range(0, len(bounds) - 1, 2)
        ]
        if not kept or any(len(c) == 0 for c in kept):
            return False
        out = np.concatenate(kept) if len(kept) > 1 else kept[0]
        fd, tmp = tempfile.mkstemp(suffix=".wav", dir=str(wav_path.parent))
        os.close(fd)
        try:
            sf.write(tmp, out, sr)
            os.replace(tmp, wav_path)
        finally:
            if os.path.exists(tmp):
                Path(tmp).unlink()
        return True
    except Exception as e:
        log.warning(f"tts_alignment: tail trim failed for {wav_path.name}: {e}")
        return False


def _prune_and_remap(words: list[dict], ranges: list) -> None:
    """Drop words inside cut ranges and shift remaining times by removed audio."""
    def _drop_before(t: float) -> float:
        return sum(max(0.0, min(end, t) - start) for start, end in ranges)

    kept: list[dict] = []
    for w in words:
        if any(start <= w["start"] and w["end"] <= end for start, end in ranges):
            continue
        drop = _drop_before(w["start"])
        kept.append({"word": w["word"], "start": w["start"] - drop, "end": w["end"] - drop})
    words[:] = kept


def _get_alignment_model(model_name: str, device: str, compute_type: str):
    global _alignment_model, _alignment_model_name
    if _alignment_model is not None and _alignment_model_name == model_name:
        return _alignment_model
    with _alignment_lock:
        if _alignment_model is None or _alignment_model_name != model_name:
            from faster_whisper import WhisperModel

            _alignment_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _alignment_model_name = model_name
    return _alignment_model


def _substitute_reference_labels(words: list[dict], reference_text: str) -> None:
    """Replace whisper's word labels with the known-spoken text, keeping timings.

    ponytail: proportional positional mapping — whisper's word count can differ
    from the script's (merged/split tokens), and VAD may drop edge words, so
    minor drift is possible. Upgrade path: true forced alignment (whisperX)
    if subtitle word-level accuracy ever matters more than timings.
    """
    ref_tokens = reference_text.split()
    if not ref_tokens or not words:
        return
    n, m = len(words), len(ref_tokens)
    for idx, w in enumerate(words):
        w["word"] = ref_tokens[min(idx * m // n, m - 1)]


def align_audio(
    wav_path: Path,
    model_name: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    reference_text: str | None = None,
    trim_tails: bool = False,
    compression_ratio_threshold: float | None = None,
) -> Path | None:
    """Align audio and write "{wav_path}.words.json".

    ``language`` pins the transcription language (e.g. "hi"). Without it,
    faster-whisper auto-detects — and Hindi TTS is frequently mis-detected as
    Urdu, producing Perso-Arabic word labels instead of Devanagari.

    ``reference_text`` is the exact text the TTS spoke. Whisper base still
    emits Perso-Arabic script for Hinglish audio even with language="hi", so
    when the true text is known its words become the labels and whisper only
    contributes timings — labels can never come out in the wrong script.

    ``trim_tails`` additionally cuts TTS drawl artifacts (गगगग/गई at segment
    edges) out of the WAV before timestamps are written. Requires
    ``reference_text`` and a model >= small (weak models transcribe the
    drawls as real words). Skipped silently otherwise.

    ``compression_ratio_threshold`` relaxes faster-whisper's gzip-compression
    hallucination guard (default 2.4). Only timestamps are used downstream
    (labels come from ``reference_text``), so a higher threshold just stops
    the whole-audio retranscribe ladder on rambling Hindi TTS segments.
    ``None`` = leave faster-whisper's own default untouched.

    Returns the JSON path on success, None on any failure (does not raise).
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        log.warning(f"tts_alignment: WAV not found: {wav_path}")
        return None

    json_path = wav_path.with_suffix(".words.json")
    try:
        json_path.resolve().relative_to(wav_path.resolve().parent)
    except ValueError:
        log.warning(f"tts_alignment: output path escapes parent directory: {json_path}")
        return None

    try:
        model = _get_alignment_model(
            model_name=model_name, device=device, compute_type=compute_type
        )
        transcribe_kwargs: dict = {
            "beam_size": 1,
            "word_timestamps": True,
            "vad_filter": True,
            "language": language,
        }
        if compression_ratio_threshold is not None:
            transcribe_kwargs["compression_ratio_threshold"] = compression_ratio_threshold
        segments_gen, _info = model.transcribe(str(wav_path), **transcribe_kwargs)

        words: list[dict] = []
        segments: list[tuple[int, int]] = []
        for seg in segments_gen:
            seg_words = getattr(seg, "words", None) or []
            idx0 = len(words)
            for w in seg_words:
                raw_word = getattr(w, "word", "") or ""
                word = raw_word.strip()
                if not word:
                    continue
                start = float(getattr(w, "start", 0.0) or 0.0)
                end = float(getattr(w, "end", 0.0) or 0.0)
                words.append({"word": word, "start": start, "end": end})
            if len(words) > idx0:
                segments.append((idx0, len(words)))

        do_trim = (
            trim_tails
            and reference_text
            and "base" not in model_name
            and "tiny" not in model_name
        )
        if do_trim:
            marks = _align_words(words, reference_text)
            ranges = _trim_ranges(words, segments, marks)
            if ranges and _cut_wav(wav_path, ranges):
                _prune_and_remap(words, ranges)
                log.info(
                    f"tts_alignment: tail trim removed {sum(e - s for s, e in ranges):.2f}s"
                    f" of drawl artifacts from {wav_path.name}"
                )

        if reference_text:
            _substitute_reference_labels(words, reference_text)

        json_path.write_text(
            json.dumps(words, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return json_path
    except Exception as e:
        log.warning(f"tts_alignment: failed for {wav_path.name}: {e}")
        return None
