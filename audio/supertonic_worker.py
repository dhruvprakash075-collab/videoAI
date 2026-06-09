"""supertonic_worker.py - Supertonic 3 TTS worker.

Two modes (mirrors omnivoice_worker.py):
  1. One-shot: --text/--output/--voice args, loads model, generates once, exits.
  2. Persistent: --serve reads line-delimited JSON requests from stdin and
     emits one JSON response per line, keeping the model loaded across segments.
     This eliminates per-segment model reload for long pipelines.

Persistent request (one JSON object per line on stdin):
  {"text": "...", "output": "path.wav",
   "voice": "M1" | "path/to/voice.json",
   "lang": "hi" | "en" | null,          # null = let Supertonic auto-detect
   "steps": 8, "speed": 1.05,
   "silence_duration": 0.3, "max_chunk_length": 120,
   "seed": -1}
Response (one JSON object per line on stdout):
  {"status": "success", "wav_path": "...", "duration_s": 3.42}
  {"status": "error", "message": "..."}
A line {"cmd": "shutdown"} cleanly stops the persistent worker.

Supertonic facts (v1.3.1, v3 model):
  * Pure ONNX, CPU-only by default ÔåÆ no VRAM pressure on the SD pipeline.
  * 31 languages, sample rate = 24000 Hz (mono).
  * Built-in voices: M1, M2, M3, M4, M5, F1, F2, F3, F4, F5.
  * Custom voices = JSON files produced by the Supertonic Voice Builder
    (https://supertonic.supertone.ai/voice-builder) ÔÇö see audio/supertonic_worker.py docstring.
  * License: MIT (code) + OpenRAIL-M (weights). Free for commercial use
    subject to OpenRAIL-M restrictions (no harm, no impersonation without
    consent, attribution required).
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the repository root is importable even when this worker is spawned
# from a different working directory or via a bare script path.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Quiet HF chatter ÔÇö parent may or may not redirect stderr depending on mode.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

import numpy as np
import soundfile as sf
from supertonic import TTS

# Supertonic v3 sample rate (mono). The TTS.synthesize() tuple is (wav, sample_rate)
# but we hard-code the known rate for explicit soundfile.write() call.
SUPERTONIC_SAMPLE_RATE = 24000
SUPPORTED_LANGS = {
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "ar", "zh", "ja", "ko", "hi", "bn", "ta", "te", "mr", "gu",
    "kn", "ml", "pa", "or", "as", "ur", "fa", "th", "vi", "id",
    "ms",
}
BUILTIN_VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]


def _load_voice_style(tts: TTS, voice: str):
    """Load a voice style ÔÇö either a built-in name or a path to a custom voice JSON.

    Raises FileNotFoundError if a custom voice path doesn't exist so the
    persistent worker can surface a clean error to the parent.
    """
    if voice in BUILTIN_VOICES:
        return tts.get_voice_style(voice_name=voice)
    # Treat as path to a Voice Builder JSON.
    p = Path(voice)
    if not p.is_file():
        raise FileNotFoundError(f"Supertonic custom voice JSON not found: {p}")
    return tts.get_voice_style_from_path(voice_style_path=str(p))


def _synthesize_once(
    tts: TTS,
    text: str,
    voice: str,
    output: str,
    lang: str | None,
    steps: int,
    speed: float,
    silence_duration: float,
    max_chunk_length: int | None,
    seed: int,
) -> dict:
    """Run a single synthesis. Returns the same dict shape as the persistent response."""
    if seed is None or seed < 0:
        # Use hash of text for deterministic chunk variability
        np.random.seed(abs(hash(text)) % (2**31 - 1))
    else:
        np.random.seed(seed)

    voice_style = _load_voice_style(tts, voice)

    # Devanagari danda fix: Supertonic's chunk_text() only splits on .!?
    # (see supertonic/utils.py:39). Hindi text uses । (single danda) or
    # ॥ (double danda) which are NOT recognized, so multi-sentence Hindi
    # collapses into one giant chunk exceeding the ONNX attention limit and
    # crashes with a Mul_13 broadcast error.
    # Pre-replace with ". " so the chunker sees real sentence boundaries.
    if isinstance(text, str):
        if "॥" in text:
            text = text.replace("॥", ". ")
        if "।" in text:
            text = text.replace("।", ". ")

    # API: synthesize(text, voice_style, total_steps, speed, max_chunk_length,
    #                 silence_duration, lang, verbose) -> (wav_2d, junk_ndarray)
    # wav is shape (1, N) ÔÇö mono with a batch dim. The real sample rate lives
    # on the TTS instance (tts.sample_rate), NOT in the returned tuple.
    wav_2d, _junk = tts.synthesize(
        text=text,
        voice_style=voice_style,
        total_steps=steps,
        speed=speed,
        max_chunk_length=max_chunk_length,
        silence_duration=silence_duration,
        lang=lang,
        verbose=False,
    )

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Match the library's own save_audio(): squeeze the batch dim, use the model's sample_rate.
    wav_1d = np.asarray(wav_2d).squeeze()
    sr = int(getattr(tts, "sample_rate", SUPERTONIC_SAMPLE_RATE))
    sf.write(str(out_path), wav_1d, sr)
    duration_s = float(len(wav_1d) / sr)
    return {"status": "success", "wav_path": str(out_path), "duration_s": duration_s}


def _serve() -> int:
    """Persistent worker ÔÇö keep the model loaded and handle many requests.

    Protocol (line-delimited JSON on stdout):
      startup: {"status": "ready", "model": "supertonic-v3"}
      per-request: {"status": "success"|"error", ...}
      shutdown ack: {"status": "success", "message": "shutdown"}
    Parent reads from stdout, writes requests to stdin. A "ready" line must
    precede any request responses so the parent can wait for model load.
    """
    print("[supertonic] loading model (CPU ONNX)...", file=sys.stderr, flush=True)
    try:
        tts = TTS(auto_download=True)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"model load failed: {e}"}), flush=True)
        sys.exit(1)
    print(json.dumps({"status": "ready", "model": "supertonic-v3"}), flush=True)
    print("[supertonic] model loaded. ready for requests.", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "message": f"bad json: {e}"}), flush=True)
            continue

        if req.get("cmd") == "shutdown":
            print(json.dumps({"status": "success", "message": "shutdown"}), flush=True)
            break

        try:
            result = _synthesize_once(
                tts=tts,
                text=req["text"],
                voice=req.get("voice", "M1"),
                output=req["output"],
                lang=req.get("lang"),
                steps=req.get("steps", 8),
                speed=req.get("speed", 1.05),
                silence_duration=req.get("silence_duration", 0.3),
                max_chunk_length=req.get("max_chunk_length"),
                seed=req.get("seed", -1),
            )
        except Exception as e:
            result = {"status": "error", "message": str(e)}
        print(json.dumps(result), flush=True)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Supertonic 3 TTS worker (CPU ONNX). Run one-shot or as --serve."
    )
    parser.add_argument("--text", help="Plain text to synthesize (one-shot)")
    parser.add_argument("--output", help="Output .wav path (one-shot)")
    parser.add_argument(
        "--voice",
        default="M1",
        help=f"Built-in voice name ({','.join(BUILTIN_VOICES)}) or path to a custom voice JSON",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help=f"Language code (one of: {sorted(SUPPORTED_LANGS)}). Omit for auto-detect.",
    )
    parser.add_argument("--steps", type=int, default=8, help="Diffusion steps (default 8)")
    parser.add_argument("--speed", type=float, default=1.05, help="Speech rate (default 1.05)")
    parser.add_argument(
        "--silence-duration", type=float, default=0.3, help="Silence between chunks (seconds)"
    )
    parser.add_argument(
        "--max-chunk-length", type=int, default=None, help="Max chars per chunk (default auto)"
    )
    parser.add_argument("--seed", type=int, default=-1, help="RNG seed (-1 = random)")
    parser.add_argument("--serve", action="store_true", help="Run as persistent worker on stdin")
    args = parser.parse_args()

    if args.serve:
        return _serve()

    if not args.text or not args.output:
        parser.error("--text and --output are required for one-shot mode (or use --serve)")

    tts = TTS(auto_download=True)
    result = _synthesize_once(
        tts=tts,
        text=args.text,
        voice=args.voice,
        output=args.output,
        lang=args.lang,
        steps=args.steps,
        speed=args.speed,
        silence_duration=args.silence_duration,
        max_chunk_length=args.max_chunk_length,
        seed=args.seed,
    )
    print(json.dumps(result))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
