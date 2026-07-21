"""tts_alignment.py - Generate word-level timestamps for TTS output.

Runs faster-whisper (CPU int8) on a WAV file and writes word timestamps to
"{wav_path}.words.json" as:
  [{"word": str, "start": float, "end": float}, ...]

Called from TTS workers so the renderer never needs to run Whisper as a
fallback for word timing.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_alignment_model = None
_alignment_model_name = None
_alignment_lock = threading.Lock()


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
) -> Path | None:
    """Align audio and write "{wav_path}.words.json".

    ``language`` pins the transcription language (e.g. "hi"). Without it,
    faster-whisper auto-detects — and Hindi TTS is frequently mis-detected as
    Urdu, producing Perso-Arabic word labels instead of Devanagari.

    ``reference_text`` is the exact text the TTS spoke. Whisper base still
    emits Perso-Arabic script for Hinglish audio even with language="hi", so
    when the true text is known its words become the labels and whisper only
    contributes timings — labels can never come out in the wrong script.

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
        segments_gen, _info = model.transcribe(
            str(wav_path),
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            language=language,
        )

        words: list[dict] = []
        for seg in segments_gen:
            seg_words = getattr(seg, "words", None) or []
            for w in seg_words:
                raw_word = getattr(w, "word", "") or ""
                word = raw_word.strip()
                if not word:
                    continue
                start = float(getattr(w, "start", 0.0) or 0.0)
                end = float(getattr(w, "end", 0.0) or 0.0)
                words.append({"word": word, "start": start, "end": end})

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
