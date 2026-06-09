"""indicf5_worker.py - IndicF5 TTS worker.

Two modes:
  1. One-shot: --text-file/--output args, loads model, generates once, exits.
  2. Persistent (--serve): reads line-delimited JSON requests from stdin and emits
     one JSON response per line, keeping the model loaded across many segments.

Persistent request (one JSON object per line on stdin):
  {"text": "...", "output": "path.wav", "ref_audio": "...", "ref_text": "",
   "sample_rate": 24000, "nfe_step": 16, "speed": 1.0, "max_chars_per_chunk": 220}
Response (one JSON object per line on stdout):
  {"status": "success", "wav_path": "...", "word_timestamps": "..."}
  {"status": "error", "message": "..."}
A line {"cmd": "shutdown"} cleanly stops the persistent worker.

Install (via setup_indicf5.ps1):
  conda create -n indicf5 python=3.10 -y
  conda activate indicf5
  pip install git+https://github.com/ai4bharat/IndicF5.git soundfile numpy
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ.setdefault("WANDB_CONSOLE", "off")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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


def _chunk_text(text: str, max_chars: int = 220) -> list[str]:
    """Split text into chunks on sentence boundaries (Devanagari compatible)."""
    import re
    chunks = []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentence_end = re.compile(r"[।.!?]+")
    parts = sentence_end.split(text)

    current_chunk = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        test_chunk = (current_chunk + " " + part).strip() if current_chunk else part
        if len(test_chunk) <= max_chars:
            current_chunk = test_chunk
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(part) <= max_chars:
                current_chunk = part
            else:
                for i in range(0, len(part), max_chars):
                    subpart = part[i:i + max_chars]
                    if i + max_chars < len(part):
                        chunks.append(subpart)
                    else:
                        current_chunk = subpart

    if current_chunk:
        chunks.append(current_chunk)

    return chunks if chunks else [text[:max_chars]]


def _load_model(model_id: str, cache_dir: str, device: str = "cuda"):
    """Load IndicF5 model from HuggingFace."""
    import torch
    from transformers import AutoModel

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    print(json.dumps({"status": "info", "message": f"Loading IndicF5 from {model_id}..."}), flush=True)

    model = AutoModel.from_pretrained(
        model_id,
        trust_remote_code=True,
        cache_dir=cache_dir
    )

    if device == "cuda" and torch.cuda.is_available():
        model = model.to("cuda")

    actual_device = next(model.parameters()).device
    print(json.dumps({"status": "info", "message": f"IndicF5 loaded on {actual_device}"}), flush=True)

    return model, str(actual_device)


def _synthesize(
    model,
    text: str,
    output: str,
    ref_audio: str = "",
    ref_text: str = "",
    sample_rate: int = 24000,
    nfe_step: int = 16,
    speed: float = 1.0,
    max_chars_per_chunk: int = 220,
) -> str:
    """Generate one audio file using IndicF5. Returns output path (str)."""
    import numpy as np
    import soundfile as sf

    out_path = Path(output)
    if out_path.is_dir():
        out_path = out_path / f"indicf5_{uuid.uuid4().hex[:8]}.wav"
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = _chunk_text(text, max_chars_per_chunk)
    if hasattr(model, "config") and hasattr(model.config, "speed"):
        model.config.speed = speed

    if len(chunks) == 1:
        audio = model(
            text,
            ref_audio_path=ref_audio if (ref_audio and Path(ref_audio).exists()) else None,
            ref_text=ref_text if ref_text else None,
        )
    else:
        audio_chunks = []
        for i, chunk in enumerate(chunks):
            print(json.dumps({"status": "progress", "chunk": i + 1, "total": len(chunks)}), flush=True)
            chunk_audio = model(
                chunk,
                ref_audio_path=ref_audio if (ref_audio and Path(ref_audio).exists()) else None,
                ref_text=ref_text if ref_text else None,
            )
            audio_chunks.append(chunk_audio)

        gap_samples = int(sample_rate * 0.15)
        gap = np.zeros(gap_samples, dtype=np.float32)

        result = [audio_chunks[0]]
        for i in range(1, len(audio_chunks)):
            result.append(gap)
            result.append(audio_chunks[i])
        audio = np.concatenate(result)

    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0

    audio = np.array(audio, dtype=np.float32)
    sf.write(str(out_path), audio, sample_rate)

    return str(out_path)


def _run_persistent(model_id: str, cache_dir: str, device: str):
    """Persistent mode: load model once, serve line-delimited JSON requests."""
    try:
        model, _ = _load_model(model_id, cache_dir, device)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"IndicF5 model load failed: {e}"}), flush=True)
        sys.exit(1)

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
                text=text,
                output=req.get("output", "tts_output"),
                ref_audio=req.get("ref_audio", ""),
                ref_text=req.get("ref_text", "") or "",
                sample_rate=int(req.get("sample_rate", 24000)),
                nfe_step=int(req.get("nfe_step", 16)),
                speed=float(req.get("speed", 1.0)),
                max_chars_per_chunk=int(req.get("max_chars_per_chunk", 220)),
            )
            word_timestamps = _maybe_align(wav)
            print(
                json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}),
                flush=True,
            )
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)


def _run_oneshot(args):
    """Legacy one-shot mode (loads model, generates once, exits)."""
    try:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
        model, _ = _load_model(args.model_id, args.cache_dir, args.device)
        wav = _synthesize(
            model,
            text=text,
            output=args.output,
            ref_audio=args.ref_audio,
            ref_text=args.ref_text or "",
            sample_rate=args.sample_rate,
            nfe_step=args.nfe_step,
            speed=args.speed,
            max_chars_per_chunk=args.max_chars,
        )
        word_timestamps = _maybe_align(wav)
        print(json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="IndicF5 TTS worker")
    parser.add_argument("--serve", action="store_true", help="Persistent mode: load model once, serve stdin JSON requests")
    parser.add_argument("--model-id", dest="model_id", default="ai4bharat/IndicF5", help="HuggingFace model ID")
    parser.add_argument("--cache-dir", dest="cache_dir", default="hf_cache/indicf5", help="HF cache directory")
    parser.add_argument("--device", dest="device", default="cuda", help="Device (cuda/cpu)")
    parser.add_argument("--text-file", dest="text_file")
    parser.add_argument("--output", default="tts_output")
    parser.add_argument("--ref-audio", dest="ref_audio", default="", help="Reference audio for voice cloning")
    parser.add_argument("--ref-text", dest="ref_text", default="", help="Transcript of reference audio")
    parser.add_argument("--sample-rate", dest="sample_rate", type=int, default=24000)
    parser.add_argument("--nfe-step", dest="nfe_step", type=int, default=16)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--max-chars", dest="max_chars", type=int, default=220)
    args = parser.parse_args()

    if args.serve:
        _run_persistent(args.model_id, args.cache_dir, args.device)
    else:
        if not args.text_file or not args.output:
            print(json.dumps({"status": "error", "message": "one-shot mode requires --text-file and --output"}))
            sys.exit(1)
        _run_oneshot(args)


if __name__ == "__main__":
    main()
