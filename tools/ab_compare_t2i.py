"""tools/ab_compare_t2i.py

Side-by-side A/B test: AnyLoRA (SD 1.5) vs Bonsai Image 4B on the same prompt.
Used to evaluate whether Bonsai's FLUX-quality output justifies the swap on a 6GB GPU.

Usage:
    venv\\Scripts\\python.exe tools\ab_compare_t2i.py \\
        --prompt "A young man with brown eyes standing on a mountain" \\
        --style "semi-realistic, Arcane-style influenced, painterly" \\
        --steps 12 --seed 42 --out studio_outputs\\ab_compare

If Bonsai is not installed, the script runs AnyLoRA only and prints what to install.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _log(msg: str) -> None:
    print(f"[ab_compare] {msg}", flush=True)


def _vram_gb() -> float:
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / 1e9, 2)
    except Exception as exc:
        print(f"[debug] VRAM read skipped: {exc}", file=sys.stderr)
    return 0.0


def _free_vram() -> None:
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as exc:
        print(f"[debug] VRAM cleanup skipped: {exc}", file=sys.stderr)


def _composite(images: list, labels: list, path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    if not images:
        return
    w, h = images[0].size
    pad = 24
    label_h = 40
    canvas = Image.new(
        "RGB", (w * len(images) + pad * (len(images) + 1), h + label_h + pad * 2), "white"
    )
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    for i, (img, label) in enumerate(zip(images, labels, strict=True)):
        x = pad + i * (w + pad)
        canvas.paste(img, (x, pad))
        draw.text((x + 8, h + pad + 8), label, fill="black", font=font)
    canvas.save(path, "PNG", optimize=True)
    _log(f"saved composite: {path}")


def _run_any_lora(
    prompt: str, neg_prompt: str, style: str, steps: int, seed: int, w: int, h: int, out_dir: Path
) -> dict:
    """Run AnyLoRA (SD 1.5-based) on the prompt. Returns timing + path."""
    import torch
    from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

    model_id = "Lykon/AnyLoRA"
    _log(f"loading AnyLoRA from {model_id}...")
    t0 = time.time()
    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.to("cuda")
    pipe.enable_xformers_memory_efficient_attention()
    load_secs = time.time() - t0
    _log(f"AnyLoRA loaded in {load_secs:.1f}s, vram_alloc={_vram_gb()}GB")

    full_prompt = f"{prompt}, {style}" if style else prompt
    g = torch.Generator(device="cuda").manual_seed(seed)
    _log(f"AnyLoRA generating {w}x{h} @ steps={steps}, seed={seed}...")
    t0 = time.time()
    result = pipe(
        prompt=full_prompt,
        negative_prompt=neg_prompt,
        num_inference_steps=steps,
        guidance_scale=6.0,
        width=w,
        height=h,
        generator=g,
    )
    gen_secs = time.time() - t0
    img = result.images[0]
    path = out_dir / "any_lora.png"
    img.save(path, "PNG", optimize=True)
    info = {
        "model": "anyLoRA (SD 1.5)",
        "model_id": model_id,
        "image": str(path),
        "load_secs": round(load_secs, 1),
        "gen_secs": round(gen_secs, 1),
        "vram_peak_gb": _vram_gb(),
        "resolution": f"{w}x{h}",
        "steps": steps,
        "seed": seed,
    }
    _log(f"AnyLoRA done in {gen_secs:.1f}s -> {path}")
    del pipe
    _free_vram()
    return info


def _run_bonsai(
    prompt: str, neg_prompt: str, style: str, steps: int, seed: int, w: int, h: int, out_dir: Path
) -> dict | None:
    """Run Bonsai Image 4B (ternary) if installed. Returns timing + path or None."""
    try:
        import bonsai  # noqa: F401
    except ImportError:
        _log("Bonsai not installed. To install:")
        _log("  pip install bonsai-image gemlite hqq triton-windows")
        _log("  huggingface-cli download prism-ml/bonsai-image-ternary-4B-gemlite-2bit")
        return None

    _log("loading Bonsai Image 4B (ternary gemlite)...")
    t0 = time.time()
    try:
        from bonsai import BonsaiPipeline

        pipe = BonsaiPipeline.from_pretrained(
            "prism-ml/bonsai-image-ternary-4B-gemlite-2bit",
            torch_dtype="auto",
            device="cuda",
        )
    except Exception as e:
        _log(f"Bonsai load failed: {e}")
        return None
    load_secs = time.time() - t0
    _log(f"Bonsai loaded in {load_secs:.1f}s, vram_alloc={_vram_gb()}GB")

    full_prompt = f"{prompt}, {style}" if style else prompt
    _log(f"Bonsai generating {w}x{h} @ steps={steps}, seed={seed}...")
    t0 = time.time()
    try:
        result = pipe(
            prompt=full_prompt,
            negative_prompt=neg_prompt,
            num_inference_steps=steps,
            guidance_scale=3.5,
            width=w,
            height=h,
            seed=seed,
        )
    except Exception as e:
        _log(f"Bonsai generation failed: {e}")
        return None
    gen_secs = time.time() - t0
    img = result.images[0] if hasattr(result, "images") else result
    path = out_dir / "bonsai.png"
    img.save(path, "PNG", optimize=True)
    info = {
        "model": "Bonsai Image 4B (ternary)",
        "model_id": "prism-ml/bonsai-image-ternary-4B-gemlite-2bit",
        "image": str(path),
        "load_secs": round(load_secs, 1),
        "gen_secs": round(gen_secs, 1),
        "vram_peak_gb": _vram_gb(),
        "resolution": f"{w}x{h}",
        "steps": steps,
        "seed": seed,
    }
    _log(f"Bonsai done in {gen_secs:.1f}s -> {path}")
    del pipe
    _free_vram()
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B test AnyLoRA vs Bonsai T2I on the same prompt")
    ap.add_argument("--prompt", required=True, help="main subject prompt")
    ap.add_argument(
        "--style",
        default="semi-realistic, Arcane-style influenced, painterly, dramatic cinematic lighting, detailed faces, atmospheric depth",
        help="style suffix appended to both prompts",
    )
    ap.add_argument(
        "--neg",
        default="photorealistic, real life, 3d, 3d render, photograph, photography, real-world textures, digital clay, lowres, bad anatomy, bad hands, text, error, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry",
        help="negative prompt",
    )
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=432)
    ap.add_argument("--out", default="studio_outputs/ab_compare", help="output dir")
    ap.add_argument("--skip-bonsai", action="store_true", help="only run AnyLoRA")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _log(f"output dir: {out_dir}")
    _log(f"prompt: {args.prompt!r}")
    _log(f"style:  {args.style!r}")
    _log(f"steps={args.steps}, seed={args.seed}, {args.width}x{args.height}")

    results: list[dict] = []
    images: list = []
    labels: list[str] = []

    _log("--- AnyLoRA (current SD 1.5) ---")
    any_info = _run_any_lora(
        args.prompt,
        args.neg,
        args.style,
        args.steps,
        args.seed,
        args.width,
        args.height,
        out_dir,
    )
    if any_info:
        results.append(any_info)
        from PIL import Image

        images.append(Image.open(any_info["image"]))
        labels.append(
            f"anyLoRA (SD 1.5)\n{any_info['gen_secs']}s, vram={any_info['vram_peak_gb']}GB"
        )

    if not args.skip_bonsai:
        _log("--- Bonsai Image 4B (FLUX.2 Klein ternary) ---")
        bonsai_info = _run_bonsai(
            args.prompt,
            args.neg,
            args.style,
            args.steps,
            args.seed,
            args.width,
            args.height,
            out_dir,
        )
        if bonsai_info:
            results.append(bonsai_info)
            from PIL import Image

            images.append(Image.open(bonsai_info["image"]))
            labels.append(
                f"Bonsai 4B (ternary)\n{bonsai_info['gen_secs']}s, vram={bonsai_info['vram_peak_gb']}GB"
            )

    if len(images) >= 2:
        _composite(images, labels, out_dir / "side_by_side.png")
    elif len(images) == 1:
        _log(f"only one model produced output: {labels[0].splitlines()[0]}")

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    _log(f"report: {report_path}")

    print("\n=== A/B SUMMARY ===")
    for r in results:
        print(
            f"  {r['model']:35s} | {r['gen_secs']:5.1f}s | vram {r['vram_peak_gb']}GB | {r['image']}"
        )
    if len(results) == 1:
        print("\n[note] only AnyLoRA ran. To test Bonsai:")
        print("  venv\\Scripts\\python.exe -m pip install bonsai-image gemlite hqq triton-windows")
        print(
            "  venv\\Scripts\\huggingface-cli.exe download prism-ml/bonsai-image-ternary-4B-gemlite-2bit"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
