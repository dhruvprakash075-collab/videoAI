"""benchmark.py - Production-time benchmark utility.

Measures seconds-per-image and seconds-per-segment for the current pipeline
configuration. Run before/after enabling optional changes (acceleration adapter,
upscaler, model switch) to compare performance.

Usage:
    venv\\Scripts\\python.exe -m utils.benchmark
    venv\\Scripts\\python.exe -m utils.benchmark --images-only
    venv\\Scripts\\python.exe -m utils.benchmark --tts-only
    venv\\Scripts\\python.exe -m utils.benchmark --report last

Results are saved to studio_outputs/benchmarks/ as timestamped JSON reports.
"""

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)


# ── Fixed benchmark prompts (consistent across runs for fair comparison) ────

_BENCHMARK_PROMPTS = [
    "young adult with warm brown eyes and short black hair, standing in a foggy forest, "
    "mysterious atmosphere, volumetric lighting, semi-realistic 2D animation style",

    "wide establishing shot of a gothic cathedral at night, moonlight through stained glass, "
    "dark fantasy atmosphere, cinematic composition, detailed architecture",

    "two figures facing each other in a dimly lit room, dramatic rim lighting, "
    "tension, medium shot, painterly textures, cinematic shadows",

    "close-up portrait of a wise elderly figure with grey beard and deep green eyes, "
    "warm candlelight, detailed face, semi-realistic 2D style",

    "sweeping landscape of a ruined city at dawn, fog rolling through broken towers, "
    "epic scale, wide angle, atmospheric, volumetric fog, 8k quality",
]

_BENCHMARK_TTS_TEXT = (
    "यह एक रहस्यमय रात थी... चाँदनी में कुछ अजीब सा दिख रहा था। "
    "हवा में एक अनजान सी खुशबू थी, जो पुरानी यादों को ताज़ा कर रही थी। "
    "अचानक, दूर से एक आवाज़ आई — कोई बुला रहा था।"
)


def _get_gpu_info() -> dict:
    """Collect GPU information for the benchmark report."""
    info = {"available": False}
    try:
        import torch
        if torch.cuda.is_available():
            info["available"] = True
            info["name"] = torch.cuda.get_device_name(0)
            free, total = torch.cuda.mem_get_info(0)
            info["vram_total_gb"] = round(total / (1024**3), 2)
            info["vram_free_gb"] = round(free / (1024**3), 2)
            info["cuda_version"] = torch.version.cuda or "unknown"
    except Exception as e:
        info["error"] = str(e)
    return info


def _get_config_snapshot(config: dict) -> dict:
    """Extract benchmark-relevant config values."""
    img_cfg = config.get("image_gen", {})
    accel = img_cfg.get("acceleration", {})
    upscaler = img_cfg.get("upscaler", {})
    return {
        "sd_model": img_cfg.get("sd_model_path", img_cfg.get("sd_model", "unknown")),
        "steps": img_cfg.get("steps", 12),
        "width": img_cfg.get("width", 768),
        "height": img_cfg.get("height", 432),
        "guidance_scale": img_cfg.get("guidance_scale", 6.0),
        "acceleration": accel.get("type", "none"),
        "accel_steps": accel.get("steps", 6) if accel.get("type", "none") != "none" else None,
        "upscaler": upscaler.get("model", "none"),
        "tts_engine": config.get("tts", {}).get("engine", "omnivoice"),
        "encoder": config.get("video", {}).get("encoder", "h264_nvenc"),
    }


def benchmark_images(config: dict, output_dir: Path, num_images: int = 5) -> dict:
    """Benchmark image generation speed.

    Generates a fixed set of prompts and measures time per image.

    Returns:
        Dict with timing results and per-image breakdown.
    """
    from video.image_gen.image_gen import generate_images, unload_sd_pipeline

    output_dir.mkdir(parents=True, exist_ok=True)
    prompts = _BENCHMARK_PROMPTS[:num_images]
    "; ".join(prompts)

    log.info(f"[BENCHMARK] Generating {len(prompts)} images...")
    log.info(f"[BENCHMARK] Config: steps={config.get('image_gen', {}).get('steps', 12)}, "
             f"size={config.get('image_gen', {}).get('width', 768)}x{config.get('image_gen', {}).get('height', 432)}")

    # Warm-up: first image loads the model (don't count it in per-image average)
    warmup_start = time.time()
    try:
        generate_images(prompts[0], output_dir / "warmup", config)
        warmup_time = time.time() - warmup_start
        log.info(f"[BENCHMARK] Warmup (model load + 1 image): {warmup_time:.1f}s")
    except Exception as e:
        log.exception(f"[BENCHMARK] Warmup failed: {e}")
        return {"error": str(e), "warmup_failed": True}

    # Timed run: generate all images (model already loaded)
    per_image_times = []
    total_start = time.time()

    for idx, prompt in enumerate(prompts):
        img_start = time.time()
        try:
            generate_images(prompt, output_dir / f"bench_{idx:02d}", config)
            img_time = time.time() - img_start
            per_image_times.append({
                "index": idx,
                "time_s": round(img_time, 2),
                "prompt_preview": prompt[:80],
                "success": True,
            })
            log.info(f"[BENCHMARK] Image {idx+1}/{len(prompts)}: {img_time:.2f}s")
        except Exception as e:
            img_time = time.time() - img_start
            per_image_times.append({
                "index": idx,
                "time_s": round(img_time, 2),
                "error": str(e),
                "success": False,
            })
            log.warning(f"[BENCHMARK] Image {idx+1} failed: {e}")

    total_time = time.time() - total_start
    successful = [t for t in per_image_times if t["success"]]
    avg_per_image = sum(t["time_s"] for t in successful) / len(successful) if successful else 0

    # Unload to free VRAM
    with contextlib.suppress(Exception):
        unload_sd_pipeline()

    return {
        "total_images": len(prompts),
        "successful": len(successful),
        "failed": len(prompts) - len(successful),
        "warmup_s": round(warmup_time, 2),
        "total_generation_s": round(total_time, 2),
        "avg_per_image_s": round(avg_per_image, 2),
        "per_image": per_image_times,
    }


def benchmark_tts(config: dict, output_dir: Path) -> dict:
    """Benchmark TTS generation speed.

    Generates a short Hindi narration clip and measures time.

    Returns:
        Dict with timing results.
    """
    from audio.audio_proxy import tts_generate

    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[BENCHMARK] Generating TTS sample ({len(_BENCHMARK_TTS_TEXT)} chars)...")
    engine = config.get("tts", {}).get("engine", "omnivoice")
    log.info(f"[BENCHMARK] TTS engine: {engine}")

    tts_start = time.time()
    try:
        result = tts_generate(
            _BENCHMARK_TTS_TEXT,
            lang="hi",
            output_dir=output_dir,
            speed=0.9,
        )
        tts_time = time.time() - tts_start
        wav_path = result.get("wav_path") if isinstance(result, dict) else result

        # Get audio duration
        audio_duration = 0.0
        try:
            from utils import get_audio_duration
            audio_duration = get_audio_duration(Path(wav_path))
        except Exception:
            pass

        log.info(f"[BENCHMARK] TTS complete: {tts_time:.2f}s → {audio_duration:.1f}s audio")

        return {
            "engine": engine,
            "input_chars": len(_BENCHMARK_TTS_TEXT),
            "generation_time_s": round(tts_time, 2),
            "audio_duration_s": round(audio_duration, 2),
            "realtime_factor": round(audio_duration / tts_time, 2) if tts_time > 0 else 0,
            "wav_path": str(wav_path),
            "success": True,
        }
    except Exception as e:
        tts_time = time.time() - tts_start
        log.exception(f"[BENCHMARK] TTS failed: {e}")
        return {
            "engine": engine,
            "generation_time_s": round(tts_time, 2),
            "error": str(e),
            "success": False,
        }


def benchmark_assembly(config: dict, output_dir: Path) -> dict:
    """Benchmark video assembly speed (Ken Burns + subtitle burn).

    Uses a pre-generated silent audio + black frames to measure pure assembly time.

    Returns:
        Dict with timing results.
    """
    import subprocess

    from video.renderer.assembler import create_segment_mp4

    output_dir.mkdir(parents=True, exist_ok=True)

    # Create a 10-second silent audio file
    audio_path = output_dir / "bench_silence.wav"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", "10", str(audio_path)
        ], capture_output=True, check=True, timeout=30)
    except Exception as e:
        return {"error": f"Could not create test audio: {e}", "success": False}

    # Create 5 simple test images (solid colors)
    from PIL import Image
    img_paths = []
    colors = [(30, 30, 50), (50, 30, 30), (30, 50, 30), (50, 50, 30), (30, 30, 70)]
    w = config.get("image_gen", {}).get("width", 768)
    h = config.get("image_gen", {}).get("height", 432)
    for idx, color in enumerate(colors):
        img = Image.new("RGB", (w, h), color)
        img_path = output_dir / f"bench_img_{idx:02d}.png"
        img.save(str(img_path))
        img_paths.append(img_path)

    script = "This is a benchmark test for video assembly timing measurement."

    log.info(f"[BENCHMARK] Assembling 10s segment with {len(img_paths)} images...")
    asm_start = time.time()
    try:
        mp4 = create_segment_mp4(
            seg_num=99,
            audio=audio_path,
            script=script,
            out_dir=output_dir,
            config=config,
            images=img_paths,
        )
        asm_time = time.time() - asm_start
        log.info(f"[BENCHMARK] Assembly complete: {asm_time:.2f}s")
        return {
            "duration_s": 10.0,
            "images": len(img_paths),
            "assembly_time_s": round(asm_time, 2),
            "output": str(mp4),
            "success": True,
        }
    except Exception as e:
        asm_time = time.time() - asm_start
        log.exception(f"[BENCHMARK] Assembly failed: {e}")
        return {
            "assembly_time_s": round(asm_time, 2),
            "error": str(e),
            "success": False,
        }


def run_full_benchmark(config: dict, images_only: bool = False,
                       tts_only: bool = False) -> dict:
    """Run the complete benchmark suite and save results.

    Args:
        config: Full pipeline config dict.
        images_only: Only benchmark image generation.
        tts_only: Only benchmark TTS.

    Returns:
        Full benchmark report dict.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bench_dir = Path("studio_outputs") / "benchmarks" / timestamp
    bench_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "gpu": _get_gpu_info(),
        "config": _get_config_snapshot(config),
        "results": {},
    }

    if not tts_only:
        log.info("=" * 60)
        log.info("  IMAGE GENERATION BENCHMARK")
        log.info("=" * 60)
        report["results"]["image_gen"] = benchmark_images(
            config, bench_dir / "images"
        )

    if not images_only:
        log.info("=" * 60)
        log.info("  TTS BENCHMARK")
        log.info("=" * 60)
        report["results"]["tts"] = benchmark_tts(config, bench_dir / "tts")

    if not images_only and not tts_only:
        log.info("=" * 60)
        log.info("  ASSEMBLY BENCHMARK")
        log.info("=" * 60)
        report["results"]["assembly"] = benchmark_assembly(
            config, bench_dir / "assembly"
        )

    # Save report
    report_path = bench_dir / "benchmark_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Also save to a latest symlink-like file for easy access
    latest_path = Path("studio_outputs") / "benchmarks" / "latest_report.json"
    latest_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Print summary
    _print_summary(report)

    log.info(f"\n[BENCHMARK] Full report saved: {report_path}")
    return report


def compare_reports(report_a: dict, report_b: dict) -> str:
    """Compare two benchmark reports and return a human-readable diff."""
    lines = []
    lines.append("=" * 60)
    lines.append("  BENCHMARK COMPARISON")
    lines.append("=" * 60)
    lines.append(f"  A: {report_a.get('timestamp', '?')}")
    lines.append(f"  B: {report_b.get('timestamp', '?')}")
    lines.append("")

    # Config diff
    cfg_a = report_a.get("config", {})
    cfg_b = report_b.get("config", {})
    config_diffs = []
    for key in set(list(cfg_a.keys()) + list(cfg_b.keys())):
        va = cfg_a.get(key)
        vb = cfg_b.get(key)
        if va != vb:
            config_diffs.append(f"    {key}: {va} → {vb}")
    if config_diffs:
        lines.append("  Config changes:")
        lines.extend(config_diffs)
        lines.append("")

    # Image gen comparison
    img_a = report_a.get("results", {}).get("image_gen", {})
    img_b = report_b.get("results", {}).get("image_gen", {})
    if img_a and img_b:
        avg_a = img_a.get("avg_per_image_s", 0)
        avg_b = img_b.get("avg_per_image_s", 0)
        diff_pct = ((avg_b - avg_a) / avg_a * 100) if avg_a > 0 else 0
        direction = "faster" if diff_pct < 0 else "slower"
        lines.append("  Image Generation:")
        lines.append(f"    A: {avg_a:.2f}s/image  |  B: {avg_b:.2f}s/image")
        lines.append(f"    Change: {abs(diff_pct):.1f}% {direction}")
        lines.append("")

    # TTS comparison
    tts_a = report_a.get("results", {}).get("tts", {})
    tts_b = report_b.get("results", {}).get("tts", {})
    if tts_a and tts_b:
        time_a = tts_a.get("generation_time_s", 0)
        time_b = tts_b.get("generation_time_s", 0)
        diff_pct = ((time_b - time_a) / time_a * 100) if time_a > 0 else 0
        direction = "faster" if diff_pct < 0 else "slower"
        lines.append("  TTS Generation:")
        lines.append(f"    A: {time_a:.2f}s  |  B: {time_b:.2f}s")
        lines.append(f"    Change: {abs(diff_pct):.1f}% {direction}")
        lines.append("")

    # Assembly comparison
    asm_a = report_a.get("results", {}).get("assembly", {})
    asm_b = report_b.get("results", {}).get("assembly", {})
    if asm_a and asm_b:
        time_a = asm_a.get("assembly_time_s", 0)
        time_b = asm_b.get("assembly_time_s", 0)
        diff_pct = ((time_b - time_a) / time_a * 100) if time_a > 0 else 0
        direction = "faster" if diff_pct < 0 else "slower"
        lines.append("  Video Assembly:")
        lines.append(f"    A: {time_a:.2f}s  |  B: {time_b:.2f}s")
        lines.append(f"    Change: {abs(diff_pct):.1f}% {direction}")

    lines.append("=" * 60)
    return "\n".join(lines)


def _print_summary(report: dict) -> None:
    """Print a concise benchmark summary to console."""
    print("\n" + "=" * 60)
    print("  BENCHMARK RESULTS SUMMARY")
    print("=" * 60)

    gpu = report.get("gpu", {})
    if gpu.get("available"):
        print(f"  GPU: {gpu.get('name', '?')} ({gpu.get('vram_total_gb', '?')} GB)")
    else:
        print("  GPU: Not available (CPU mode)")

    cfg = report.get("config", {})
    print(f"  Model: {cfg.get('sd_model', '?')}")
    print(f"  Steps: {cfg.get('steps', '?')} | Size: {cfg.get('width', '?')}x{cfg.get('height', '?')}")
    accel = cfg.get("acceleration", "none")
    if accel != "none":
        print(f"  Acceleration: {accel} ({cfg.get('accel_steps', '?')} steps)")
    print(f"  TTS: {cfg.get('tts_engine', '?')}")
    print()

    results = report.get("results", {})

    img = results.get("image_gen", {})
    if img:
        if img.get("error"):
            print(f"  Image Gen: FAILED — {img['error']}")
        else:
            print(f"  Image Gen: {img.get('avg_per_image_s', '?')}s/image "
                  f"({img.get('successful', 0)}/{img.get('total_images', 0)} successful)")
            print(f"    Warmup (model load): {img.get('warmup_s', '?')}s")
            print(f"    Total generation: {img.get('total_generation_s', '?')}s")

    tts = results.get("tts", {})
    if tts:
        if tts.get("success"):
            print(f"  TTS: {tts.get('generation_time_s', '?')}s → "
                  f"{tts.get('audio_duration_s', '?')}s audio "
                  f"(realtime factor: {tts.get('realtime_factor', '?')}x)")
        else:
            print(f"  TTS: FAILED — {tts.get('error', '?')}")

    asm = results.get("assembly", {})
    if asm:
        if asm.get("success"):
            print(f"  Assembly: {asm.get('assembly_time_s', '?')}s for "
                  f"{asm.get('duration_s', '?')}s video ({asm.get('images', '?')} images)")
        else:
            print(f"  Assembly: FAILED — {asm.get('error', '?')}")

    print("=" * 60)


def _load_latest_report() -> dict | None:
    """Load the most recent benchmark report."""
    latest = Path("studio_outputs") / "benchmarks" / "latest_report.json"
    if latest.exists():
        return json.loads(latest.read_text(encoding="utf-8"))
    return None


def main():
    """CLI entry point for the benchmark utility."""
    # Apply compatibility patches
    try:
        from utils.compatibility import apply_all_patches
        apply_all_patches()
    except ImportError:
        pass

    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Video.AI Production-Time Benchmark"
    )
    parser.add_argument("--images-only", action="store_true",
                        help="Only benchmark image generation")
    parser.add_argument("--tts-only", action="store_true",
                        help="Only benchmark TTS generation")
    parser.add_argument("--report", choices=["last", "compare"],
                        help="Show last report or compare last two")
    parser.add_argument("--compare-with", type=str, default=None,
                        help="Path to a previous report JSON to compare against latest")
    args = parser.parse_args()

    if args.report == "last":
        report = _load_latest_report()
        if report:
            _print_summary(report)
        else:
            print("No benchmark reports found. Run a benchmark first.")
        return

    if args.report == "compare" or args.compare_with:
        latest = _load_latest_report()
        if not latest:
            print("No latest report found.")
            return
        if args.compare_with:
            compare_path = Path(args.compare_with)
            if not compare_path.exists():
                print(f"Report not found: {compare_path}")
                return
            other = json.loads(compare_path.read_text(encoding="utf-8"))
        else:
            # Find the second-most-recent report
            bench_dir = Path("studio_outputs") / "benchmarks"
            reports = sorted(
                [d for d in bench_dir.iterdir() if d.is_dir() and d.name != "latest_report.json"],
                reverse=True
            )
            if len(reports) < 2:
                print("Need at least 2 benchmark runs to compare.")
                return
            other_path = reports[1] / "benchmark_report.json"
            if not other_path.exists():
                print(f"Previous report not found: {other_path}")
                return
            other = json.loads(other_path.read_text(encoding="utf-8"))
        print(compare_reports(other, latest))
        return

    # Run benchmark
    from config import load_config
    config = load_config()

    print("\n" + "=" * 60)
    print("  Video.AI Production-Time Benchmark")
    print("=" * 60)
    print("  This will generate test images and audio to measure speed.")
    print("  Results saved to: studio_outputs/benchmarks/")
    print("=" * 60 + "\n")

    run_full_benchmark(config, images_only=args.images_only, tts_only=args.tts_only)


if __name__ == "__main__":
    main()
