"""f5_worker.py - F5-TTS worker (T1).

Two modes (mirrors omnivoice_worker.py design):
  1. One-shot (legacy): --text-file/--output args, loads model, generates once, exits.
  2. Persistent (--serve): reads line-delimited JSON requests from stdin and emits
     one JSON response per line, keeping the model loaded across many segments.

Persistent request (one JSON object per line on stdin):
  {"text": "...", "output": "path.wav", "voice_sample": "...", "ref_text": "",
   "nfe_step": 16, "speed": 1.0}
Response (one JSON object per line on stdout):
  {"status": "success", "wav_path": "..."} | {"status": "error", "message": "..."}
A line {"cmd": "shutdown"} cleanly stops the persistent worker.

Install (W0 / setup_f5.ps1):
  venv\\Scripts\\pip.exe install f5-tts soundfile
  venv\\Scripts\\huggingface-cli.exe download SPRINGLab/F5-Hindi-24KHz --local-dir hf_cache\\f5_hindi
"""

import argparse
import contextlib
import json
import os
import sys
import uuid
from pathlib import Path


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

# Quiet HF progress bars in worker subprocess
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
# Allow CUDA allocator to use expandable segments (same as omnivoice_worker)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# Disable wandb entirely — f5_tts pulls it in and it wraps stdout (breaks our JSON
# protocol and crashes on Devanagari prints under the Windows cp1252 console).
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("WANDB_DISABLED", "true")
os.environ.setdefault("WANDB_CONSOLE", "off")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Force UTF-8 on stdout/stderr so f5_tts's internal print() of Devanagari text
# doesn't crash on the Windows cp1252 console (UnicodeEncodeError).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _install_torchaudio_soundfile_patch():
    """Replace torchaudio.load with a soundfile-backed loader at import time.

    torchaudio 2.11 routes audio I/O through torchcodec, which needs FFmpeg
    shared DLLs not present on this Windows box (libtorchcodec_core*.dll fails).
    soundfile reads WAV fine, so we swap torchaudio.load to use it. Must run
    BEFORE f5_tts.infer.utils_infer calls torchaudio.load.
    """
    try:
        import soundfile as sf
        import torch
        import torchaudio

        def _sf_load(filepath, frame_offset=0, num_frames=-1, normalize=True,
                     channels_first=True, *args, **kwargs):
            # soundfile gives [T, C]; honor torchaudio's load() contract.
            data, sr = sf.read(str(filepath), dtype="float32", always_2d=True)
            if frame_offset:
                data = data[int(frame_offset):]
            if num_frames is not None and num_frames > 0:
                data = data[:int(num_frames)]
            tensor = torch.from_numpy(data.copy())  # [T, C]
            if channels_first:
                tensor = tensor.T.contiguous()       # [C, T]
            return tensor, sr

        torchaudio.load = _sf_load
        return True
    except Exception:
        return False


# Install the patch immediately at module import (before any f5 import path runs).
_TORCHAUDIO_PATCHED = _install_torchaudio_soundfile_patch()


def _resolve_model_path(model_path: str) -> str:
    """Resolve the model path, handling HuggingFace hub snapshot layout.

    HF hub stores models as:
      hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/<hash>/

    If model_path points to a 'snapshots/main' or 'snapshots/<hash>' that
    doesn't exist as a directory, walk up and find the actual snapshot folder.
    """
    p = Path(model_path)
    if p.exists() and p.is_dir():
        return str(p)

    # Try: if path ends with snapshots/main, resolve via refs/main pointer
    snapshots_dir = p.parent if p.name == "main" else p
    if not snapshots_dir.exists():
        # Walk up to find snapshots dir
        candidate = p
        for _ in range(4):
            candidate = candidate.parent
            snap = candidate / "snapshots"
            if snap.exists():
                snapshots_dir = snap
                break

    if snapshots_dir.exists() and snapshots_dir.name == "snapshots":
        # Pick the first (and usually only) snapshot hash folder
        subdirs = [d for d in snapshots_dir.iterdir() if d.is_dir()]
        if subdirs:
            return str(subdirs[0])

    # Fall back to original path (let caller handle the error)
    return model_path


def _load_model(model_path: str):
    """Load F5-TTS model from local path. Returns (model, vocoder, device)."""
    import torch
    from f5_tts.infer.utils_infer import load_model, load_vocoder
    from f5_tts.model import DiT

    # Re-assert the torchaudio->soundfile patch (avoids missing torchcodec DLLs)
    _install_torchaudio_soundfile_patch()

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # F5-TTS uses a DiT backbone + vocos vocoder
    model_path = Path(model_path)
    ckpt_file = model_path / "model_1200000.safetensors"
    if not ckpt_file.exists():
        # Prefer safetensors over .pt (smaller, safer); pick the largest match
        sft = sorted(model_path.glob("*.safetensors"), key=lambda p: p.stat().st_size, reverse=True)
        if sft:
            ckpt_file = sft[0]
        else:
            pts = sorted(model_path.glob("*.pt"), key=lambda p: p.stat().st_size, reverse=True)
            if pts:
                ckpt_file = pts[0]

    vocab_file = model_path / "vocab.txt"
    if not vocab_file.exists():
        vocab_file = None

    # Auto-detect the model architecture from the checkpoint weights.
    # F5 ships in two sizes:
    #   Base:  dim=1024, depth=22, heads=16, ff_mult=2  (e.g. F5TTS_Base)
    #   Small: dim=768,  depth=18, heads=12, ff_mult=2  (e.g. SPRINGLab Hindi)
    # Reading dim/depth from the state_dict avoids "size mismatch" load errors.
    model_cfg = _detect_dit_config(str(ckpt_file))
    log_cfg = ", ".join(f"{k}={v}" for k, v in model_cfg.items())
    print(json.dumps({"status": "info", "message": f"DiT config detected: {log_cfg}"}), flush=True)

    model = load_model(
        DiT,
        model_cfg,
        str(ckpt_file),
        mel_spec_type="vocos",
        vocab_file=str(vocab_file) if vocab_file else "",
        ode_method="euler",
        use_ema=True,
        device=device,
    )
    vocoder = load_vocoder(is_local=False, local_path="", device=device)
    return model, vocoder, device


def _detect_dit_config(ckpt_file: str) -> dict:
    """Read dim/depth/heads from the checkpoint so the model shape matches.

    Falls back to the F5 Base config if detection fails.
    """
    base = {"dim": 1024, "depth": 22, "heads": 16, "ff_mult": 2, "text_dim": 512, "conv_layers": 4}
    try:
        import torch
        sd = None
        if ckpt_file.endswith(".safetensors"):
            from safetensors.torch import load_file
            sd = load_file(ckpt_file)
        else:
            obj = torch.load(ckpt_file, map_location="cpu", weights_only=False)
            # EMA checkpoints nest weights under "ema_model_state_dict" or "model_state_dict"
            for key in ("ema_model_state_dict", "model_state_dict", "model"):
                if isinstance(obj, dict) and key in obj:
                    obj = obj[key]
                    break
            sd = obj

        if not isinstance(sd, dict):
            return base

        # Strip common prefixes (ema_model., model.) to find transformer keys
        def _find(suffix):
            for k, v in sd.items():
                if k.endswith(suffix):
                    return v
            return None

        # dim: from time_embed.time_mlp.0.weight shape [dim, 256]
        time_w = _find("time_embed.time_mlp.0.weight")
        dim = int(time_w.shape[0]) if time_w is not None else base["dim"]

        # depth: count transformer_blocks.N.* (max N + 1)
        depth = 0
        for k in sd:
            if "transformer_blocks." in k:
                try:
                    n = int(k.split("transformer_blocks.")[1].split(".")[0])
                    depth = max(depth, n + 1)
                except (ValueError, IndexError):
                    pass
        if depth == 0:
            depth = base["depth"]

        # heads: standard F5 uses dim/64 heads (1024->16, 768->12)
        heads = max(1, dim // 64)

        return {"dim": dim, "depth": depth, "heads": heads, "ff_mult": 2,
                    "text_dim": 512, "conv_layers": 4}
    except Exception:
        return base


def _synthesize(model, vocoder, device, text: str, output: str,
                voice_sample: str = "", ref_text: str = "",
                nfe_step: int = 16, speed: float = 1.0) -> str:
    """Generate one audio file using F5-TTS. Returns output path (str)."""
    import soundfile as sf
    from f5_tts.infer.utils_infer import infer_process, preprocess_ref_audio_text

    # Ensure torchaudio.load is soundfile-backed before any audio I/O
    _install_torchaudio_soundfile_patch()

    out_path = Path(output)
    if out_path.is_dir():
        out_path = out_path / f"f5_{uuid.uuid4().hex[:8]}.wav"
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # show_info must be a CALLABLE (default is print), NOT a bool.
    # Passing False here causes "'bool' object is not callable" when f5 calls it.
    _quiet = lambda *a, **k: None

    # Prepare reference audio + text for voice cloning
    ref_audio_path = voice_sample if (voice_sample and Path(voice_sample).exists()) else None
    if ref_audio_path:
        ref_audio, ref_text_used = preprocess_ref_audio_text(
            ref_audio_path, ref_text or "", show_info=_quiet
        )
    else:
        # No reference — use a silent 1s clip (generic voice, no cloning)
        import numpy as np
        _sr = 24000
        _silence = np.zeros(int(_sr * 1.0), dtype=np.float32)
        _tmp = str(out_path.with_suffix(".ref_tmp.wav"))
        sf.write(_tmp, _silence, _sr)
        ref_audio, ref_text_used = preprocess_ref_audio_text(_tmp, ".", show_info=_quiet)
        with contextlib.suppress(Exception):
            Path(_tmp).unlink()

    audio, sr, _ = infer_process(
        ref_audio,
        ref_text_used,
        text,
        model,
        vocoder,
        mel_spec_type="vocos",
        show_info=_quiet,
        speed=speed,
        nfe_step=nfe_step,
        cfg_strength=2.0,
        sway_sampling_coef=-1.0,
        device=device,
    )

    sf.write(str(out_path), audio, sr)
    return str(out_path)


def _run_persistent(model_path: str):
    """Persistent mode: load model once, serve line-delimited JSON requests."""
    model_path = _resolve_model_path(model_path)
    try:
        model, vocoder, device = _load_model(model_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"F5 model load failed: {e}"}), flush=True)
        sys.exit(1)

    # Signal readiness
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
                model, vocoder, device,
                text=text,
                output=req.get("output", "tts_output"),
                voice_sample=req.get("voice_sample", ""),
                ref_text=req.get("ref_text", "") or "",
                nfe_step=int(req.get("nfe_step", 16)),
                speed=float(req.get("speed", 1.0)),
            )
            word_timestamps = _maybe_align(wav)
            print(json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}), flush=True)
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)


def _run_oneshot(args):
    """Legacy one-shot mode (loads model, generates once, exits)."""
    try:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
        resolved_path = _resolve_model_path(args.model_path)
        model, vocoder, device = _load_model(resolved_path)
        wav = _synthesize(
            model, vocoder, device,
            text=text,
            output=args.output,
            voice_sample=args.voice_sample,
            ref_text=args.ref_text or "",
            nfe_step=args.nfe_step,
            speed=args.speed,
        )
        word_timestamps = _maybe_align(wav)
        print(json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="F5-TTS worker")
    parser.add_argument("--serve", action="store_true",
                        help="Persistent mode: load model once, serve stdin JSON requests")
    parser.add_argument("--model-path", dest="model_path",
                        default="hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/main",
                        help="Path to F5-TTS model directory")
    parser.add_argument("--text-file", dest="text_file")
    parser.add_argument("--output", default="tts_output")
    parser.add_argument("--voice-sample", dest="voice_sample", default="",
                        help="Reference audio for voice cloning")
    parser.add_argument("--ref-text", dest="ref_text", default="",
                        help="Transcript of reference clip (skips ASR, saves VRAM)")
    parser.add_argument("--nfe-step", dest="nfe_step", type=int, default=16,
                        help="Denoising steps (lower = faster)")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    if args.serve:
        _run_persistent(args.model_path)
    else:
        if not args.text_file or not args.output:
            print(json.dumps({"status": "error",
                              "message": "one-shot mode requires --text-file and --output"}))
            sys.exit(1)
        _run_oneshot(args)


if __name__ == "__main__":
    main()
