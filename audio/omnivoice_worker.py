"""omnivoice_worker.py - OmniVoice TTS worker.

Two modes:
  1. One-shot (legacy): --text-file/--output args, loads model, generates once, exits.
  2. Persistent (B16 fix): --serve reads line-delimited JSON requests from stdin and
     emits one JSON response per line, keeping the model loaded across many segments.
     This eliminates the per-segment model reload that dominated long-run time.

Persistent request (one JSON object per line on stdin):
  {"text": "...", "output": "path.wav", "voice_sample": "...", "speed": 0.9,
   "num_step": 40, "guidance_scale": 2.5, "seed": -1}
Response (one JSON object per line on stdout):
  {"status": "success", "wav_path": "..."} | {"status": "error", "message": "..."}
A line {"cmd": "shutdown"} cleanly stops the persistent worker.
"""

import argparse
import json
import os
import random
import sys
import uuid

# OmniVoice OOM/hang fix on low-VRAM GPUs (≤8GB) — see k2-fsa/OmniVoice issue #41.
# expandable_segments lets the CUDA allocator satisfy small inference allocations
# from reserved-but-unallocated memory, preventing the fragmentation OOM/hang that
# occurs on 6GB cards during create_voice_clone_prompt(). Must be set before torch.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# Quiet transformers/whisper chatter. In persistent --serve mode the parent sends
# stderr to DEVNULL, but we also lower verbosity so nothing floods stdout either.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
from pathlib import Path

import numpy as np
import soundfile as sf
import torch


def _maybe_align(wav_path: str) -> str | None:
    try:
        from config import load_config

        cfg = load_config()
        align = cfg.get("tts", {}).get("alignment", {})
        if not align.get("enabled", True):
            return None
        from audio.tts_alignment import align_audio

        result = align_audio(
            Path(wav_path),
            model_name=align.get("model", "base"),
            device=align.get("device", "cpu"),
            compute_type=align.get("compute_type", "int8"),
        )
        return str(result) if result else None
    except Exception:
        return None


def _install_torchaudio_soundfile_patch():
    """Replace torchaudio.load with a soundfile-backed loader.

    torch 2.11 routes torchaudio.load through torchcodec, which needs FFmpeg
    shared DLLs not present on this Windows box. soundfile reads WAV fine, so
    we swap torchaudio.load to use it. Must run BEFORE omnivoice/transformers
    call torchaudio.load (e.g. Whisper ASR for the reference clip).
    """
    try:
        import torchaudio

        def _sf_load(
            filepath,
            frame_offset=0,
            num_frames=-1,
            normalize=True,
            channels_first=True,
            *args,
            **kwargs,
        ):
            data, sr = sf.read(str(filepath), dtype="float32", always_2d=True)
            if frame_offset:
                data = data[int(frame_offset) :]
            if num_frames is not None and num_frames > 0:
                data = data[: int(num_frames)]
            tensor = torch.from_numpy(data.copy())  # [T, C]
            if channels_first:
                tensor = tensor.T.contiguous()  # [C, T]
            return tensor, sr

        torchaudio.load = _sf_load
        return True
    except Exception:
        return False


# Install the patch at import time, before any omnivoice/transformers code runs.
_TORCHAUDIO_PATCHED = _install_torchaudio_soundfile_patch()


def _load_model():
    """Load the OmniVoice model once and return it."""
    from omnivoice import OmniVoice

    device = "cuda" if torch.cuda.is_available() else "cpu"
    device_map = "cuda:0" if device == "cuda" else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    return OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=device_map, dtype=dtype)


def _gen_config(num_step, guidance_scale):
    from omnivoice.models.omnivoice import OmniVoiceGenerationConfig

    return OmniVoiceGenerationConfig(
        num_step=num_step,
        guidance_scale=guidance_scale,
        # B21: chunk threshold tuned higher to reduce mid-segment seams that can
        # shift cloned-voice timbre; most narration segments stay under one chunk.
        audio_chunk_threshold=45.0,
    )


def _set_seed(seed):
    if seed is not None and seed != -1:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def _split_text_chunks(text: str, max_chars: int = 500):
    """Split text into short chunks at sentence boundaries (B21 fix).

    OmniVoice can stall when asked to synthesize a long clip in one call (internal
    chunking on long audio hangs the GPU at ~2% util). Splitting into sentence-
    bounded chunks keeps each generate() call bounded and reliable. ~500 chars
    balances reliability against per-chunk overhead (fewer chunks = faster overall).

    Boundaries: Devanagari danda (।), plus . ! ? and newlines. Chunks are packed
    up to ~max_chars so we don't over-fragment (which would seam the voice).
    """
    import re as _re

    # Split into sentences keeping the delimiter; handle Devanagari danda + Latin punctuation
    parts = _re.split(r"(?<=[।.!?\n])\s+", text.strip())
    chunks = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not cur:
            cur = p
        elif len(cur) + 1 + len(p) <= max_chars:
            cur = f"{cur} {p}"
        else:
            chunks.append(cur)
            cur = p
        # Hard-split any single piece longer than max_chars (no sentence break)
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def _prepare_ref_audio(voice_sample: str, max_seconds: float = 8.0) -> str:
    """Return a short, mono reference clip path (OmniVoice issue #41 VRAM fix).

    Long/stereo reference audio sharply increases VRAM during voice cloning on
    low-VRAM GPUs. We trim to the first `max_seconds` and downmix to mono, caching
    the result next to the original so we only do it once.
    """
    try:
        if not voice_sample or not os.path.exists(voice_sample):
            return voice_sample
        src = Path(voice_sample)
        cached = src.with_name(f"{src.stem}_ref{int(max_seconds)}s_mono.wav")
        if cached.exists():
            return str(cached)
        data, sr = sf.read(str(src), dtype="float32")
        if data.ndim > 1:  # stereo -> mono
            data = data.mean(axis=1)
        max_samples = int(max_seconds * sr)
        if len(data) > max_samples:  # trim to first N seconds
            data = data[:max_samples]
        sf.write(str(cached), data, sr)
        return str(cached)
    except Exception:
        return voice_sample  # on any error, fall back to the original


def _synthesize(
    model,
    text,
    output,
    voice_sample="",
    speed=0.85,
    num_step=40,
    guidance_scale=2.5,
    seed=-1,
    ref_text=None,
    sentence_gap_ms=None,
):
    """Generate one audio file, synthesizing in short chunks (B21 fix).

    Returns the output path (str). Long scripts are split into sentence-bounded
    chunks, each synthesized separately and concatenated, so a single long
    generation can never stall the worker.

    ref_text: transcript of the reference clip. When provided, OmniVoice does NOT
    load the Whisper ASR model (which otherwise loads a 2nd model and OOMs on 6GB
    GPUs — issue #41). Strongly recommended on low-VRAM cards.
    sentence_gap_ms: crossfade duration in ms between synthesized chunks (P4-9 fix).
                     When None, defaults to 200ms. Sourced from
                     tts.voice_profile.sentence_gap_ms in config (default 200ms).
    """
    _set_seed(seed)

    # Create the voice-clone prompt ONCE and reuse across all chunks so the cloned
    # timbre stays consistent (re-creating per chunk causes audible seams).
    # Use a short, mono reference clip + supply ref_text to avoid the Whisper load.
    voice_prompt = None
    if voice_sample and os.path.exists(voice_sample):
        ref_clip = _prepare_ref_audio(voice_sample)
        voice_prompt = model.create_voice_clone_prompt(ref_audio=ref_clip, ref_text=ref_text)

    gen_cfg = _gen_config(num_step, guidance_scale)
    chunks = _split_text_chunks(text)

    # P3-8 fix: read the actual sample rate from the model when available.
    # OmniVoice exposes model.sample_rate on most builds; fall back to 24000 if
    # the attribute is absent (older checkpoints or future API changes).
    try:
        sample_rate = int(model.sample_rate)
    except (AttributeError, TypeError, ValueError):
        sample_rate = 24000  # safe default; matches current OmniVoice checkpoint
    # P2-14 fix: crossfade chunk seams instead of a fixed silence gap.
    # P4-9 fix: use sentence_gap_ms from the request (sourced from config
    # tts.voice_profile.sentence_gap_ms, default 200ms) instead of a hardcoded
    # value.  The crossfade blends the tail of one chunk into the head of
    # the next, masking the timbre discontinuity at the boundary.
    # Default 200ms matches tts.voice_profile.sentence_gap_ms in config.yaml.
    if sentence_gap_ms is None:
        _gap_ms = 200
    else:
        _gap_ms = max(0, sentence_gap_ms)
    _xfade_samples = int((_gap_ms / 1000.0) * sample_rate)

    pieces = []
    for idx, chunk in enumerate(chunks):
        kw = {"text": chunk, "speed": speed, "generation_config": gen_cfg}
        if voice_prompt is not None:
            kw["voice_clone_prompt"] = voice_prompt
        audio_arrays = model.generate(**kw)
        audio = audio_arrays[0].astype(np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=0)

        if (
            pieces
            and _xfade_samples > 0
            and len(pieces[-1]) >= _xfade_samples
            and len(audio) >= _xfade_samples
        ):
            # Crossfade: blend the tail of the previous chunk with the head of this one.
            prev = pieces[-1]
            fade_out = np.linspace(1.0, 0.0, _xfade_samples, dtype=np.float32)
            fade_in = np.linspace(0.0, 1.0, _xfade_samples, dtype=np.float32)
            # Overwrite the tail of the previous chunk with the blended region
            prev[-_xfade_samples:] = (
                prev[-_xfade_samples:] * fade_out + audio[:_xfade_samples] * fade_in
            )
            pieces[-1] = prev
            # Append the remainder of the current chunk (skip the already-blended head)
            pieces.append(audio[_xfade_samples:])
        else:
            pieces.append(audio)

        # Progress line so the parent can see liveness (and we know it's not stalled)
        print(
            json.dumps({"status": "progress", "chunk": idx + 1, "total": len(chunks)}), flush=True
        )

    audio = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)

    out_path = Path(output)
    if out_path.is_dir():
        out_path = out_path / f"omnivoice_{uuid.uuid4().hex[:8]}.wav"
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), audio, sample_rate)
    return str(out_path)


def _run_persistent():
    """Persistent mode: load model once, serve line-delimited JSON requests (B16 fix)."""
    try:
        model = _load_model()
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"model load failed: {e}"}), flush=True)
        sys.exit(1)

    # Signal readiness so the parent knows the model is loaded
    print(json.dumps({"status": "ready"}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"status": "error", "message": "invalid JSON request"}), flush=True)
            continue

        if req.get("cmd") == "shutdown":
            print(json.dumps({"status": "shutdown"}), flush=True)
            break

        try:
            text = req.get("text", "")
            if not text and req.get("text_file"):
                text = Path(req["text_file"]).read_text(encoding="utf-8").strip()
            wav = _synthesize(
                model,
                text,
                req.get("output", "tts_output"),
                voice_sample=req.get("voice_sample", ""),
                speed=float(req.get("speed", 0.85)),
                num_step=int(req.get("num_step", 40)),
                guidance_scale=float(req.get("guidance_scale", 2.5)),
                seed=int(req.get("seed", -1)),
                ref_text=req.get("ref_text") or None,
                sentence_gap_ms=req.get("sentence_gap_ms"),
            )
            word_timestamps = _maybe_align(wav)
            print(
                json.dumps(
                    {"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}
                ),
                flush=True,
            )
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)


def _run_oneshot(args):
    """Legacy one-shot mode (loads model, generates once, exits)."""
    try:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
        model = _load_model()
        wav = _synthesize(
            model,
            text,
            args.output,
            voice_sample=args.voice_sample,
            speed=args.speed,
            num_step=args.num_step,
            guidance_scale=args.guidance_scale,
            seed=args.seed,
            ref_text=getattr(args, "ref_text", None) or None,
        )
        word_timestamps = _maybe_align(wav)
        print(
            json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps})
        )
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Persistent mode: load model once, serve stdin JSON requests",
    )
    parser.add_argument("--text-file")
    parser.add_argument("--output")
    parser.add_argument("--voice-sample", default="")
    parser.add_argument(
        "--ref-text",
        dest="ref_text",
        default="",
        help="Transcript of the reference clip — supply to skip the Whisper ASR load (VRAM fix, issue #41)",
    )
    parser.add_argument("--speed", type=float, default=0.85)
    parser.add_argument("--num-step", type=int, default=40)
    parser.add_argument("--guidance-scale", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=-1)
    args = parser.parse_args()

    if args.serve:
        _run_persistent()
    else:
        if not args.text_file or not args.output:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "one-shot mode requires --text-file and --output",
                    }
                )
            )
            sys.exit(1)
        _run_oneshot(args)


if __name__ == "__main__":
    main()
