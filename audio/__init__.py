r"""audio package — TTS generation + audio processing for Video.AI pipeline.

Submodules:
    audio_proxy: Main TTS dispatch (IndicF5 / Supertonic / OmniVoice) with fallback chain
    tts_alignment: Whisper-based word-level alignment for subtitles
    supertonic_worker: Persistent CPU ONNX worker subprocess (Supertonic 3)
    omnivoice_worker: Persistent GPU worker subprocess (OmniVoice)
    indicf5_worker: One-shot subprocess wrapper for IndicF5 (external D:\IndicF5)
    audio_fx: Mastering chain (loudnorm, ducking, SFX mix)

Config knobs (under config["tts"]):
    engine: "indicf5" | "supertonic" | "omnivoice"  (normalized via normalize_tts_engine)
    devanagari: { max_predict_tokens: 768, ... }
    supertonic: { voice: "M1", steps: 16, speed: 1.0, max_chunk_length: 100 }
    omnivoice:  { speed: 0.85, num_step: 24, guidance_scale: 2.5 }
    alignment:  { enabled: true, model: "base", device: "cpu", language: "hi" }
"""
import contextlib

# Audio processing module for Video.AI pipeline
from .audio_proxy import get_audio_duration, tts_generate

__all__ = [
    "get_audio_duration",
    "tts_generate",
]

# Degradation callback registry — breaks audio→agents import dependency.
# Pipeline sets this via set_degradation_callback() so audio modules can
# report degradations without importing UIState directly.
_degradation_callbacks: list = []


def add_degradation_callback(cb) -> None:
    if cb not in _degradation_callbacks:  # identity dedupe — pipeline re-registers per run
        _degradation_callbacks.append(cb)


def record_degradation(segment_idx: int, category: str, reason: str) -> None:
    for cb in _degradation_callbacks:
        with contextlib.suppress(Exception):
            cb(segment_idx, category, reason)
