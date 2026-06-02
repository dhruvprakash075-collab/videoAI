# Audio processing module for Video.AI pipeline
from .audio_proxy import get_audio_duration, tts_generate

__all__ = [
    "get_audio_duration",
    "tts_generate",
]
