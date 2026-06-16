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


def align_audio(
    wav_path: Path,
    model_name: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
) -> Path | None:
    """Align audio and write "{wav_path}.words.json".

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

        json_path.write_text(
            json.dumps(words, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return json_path
    except Exception as e:
        log.warning(f"tts_alignment: failed for {wav_path.name}: {e}")
        return None
