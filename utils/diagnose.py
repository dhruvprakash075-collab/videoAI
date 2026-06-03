#!/usr/bin/env python3
"""
diagnose.py - Video.AI System, GPU, and Media Diagnostics Utility.

A zero-dependency (by default) tool to analyze VRAM, active Ollama model residency,
and audio/video media file health.

Usage:
    python utils/diagnose.py gpu
    python utils/diagnose.py media <path_to_file>
    python utils/diagnose.py system
    python utils/diagnose.py all
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ANSI colors for beautiful terminal output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_info(msg: str):
    print(f"{BLUE}[INFO]{RESET} {msg}")


def log_success(msg: str):
    print(f"{GREEN}[OK] {msg}{RESET}")


def log_warn(msg: str):
    print(f"{YELLOW}[WARN] {msg}{RESET}")


def log_error(msg: str):
    print(f"{RED}[ERROR] {msg}{RESET}")


def check_ffmpeg() -> bool:
    """Verify if ffmpeg and ffprobe are available in the system path."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ── GPU & VRAM Diagnostics ──────────────────────────────────────────────────


def diagnose_gpu():
    print(f"\n{BOLD}{CYAN}=== GPU & VRAM Diagnostics ==={RESET}")

    # 1. PyTorch & CUDA Support
    try:
        import torch

        cuda_avail = torch.cuda.is_available()
        log_success(f"PyTorch imported successfully (v{torch.__version__})")
        if cuda_avail:
            device_name = torch.cuda.get_device_name(0)
            log_success(f"CUDA is AVAILABLE. Device 0: {BOLD}{device_name}{RESET}")

            # Memory Info
            try:
                free_b, total_b = torch.cuda.mem_get_info()
                free_gb = free_b / (1024**3)
                total_gb = total_b / (1024**3)
                used_gb = total_gb - free_gb

                print(f"  - Total VRAM: {total_gb:.2f} GB")
                print(f"  - Free VRAM:  {free_gb:.2f} GB")
                print(f"  - Used VRAM:  {used_gb:.2f} GB")

                # Check VRAM limits (Video.AI 6GB threshold checks)
                if total_gb < 5.5:
                    log_warn(
                        f"Running on a sub-6GB VRAM budget ({total_gb:.1f}GB). Serialization is highly recommended."
                    )
                elif total_gb <= 6.5:
                    log_info(
                        "Running on a 6GB VRAM budget (RTX 4050 typical). Eviction-based staged loop active."
                    )
                else:
                    log_success(f"Plenty of VRAM available ({total_gb:.1f}GB).")
            except Exception as mem_err:
                log_warn(f"Could not retrieve CUDA memory details: {mem_err}")
        else:
            log_warn("CUDA is NOT available to PyTorch. System will run on CPU only.")
    except ImportError:
        log_error("PyTorch is not installed in the active environment.")

    # 2. NVIDIA System Monitor (nvidia-smi)
    try:
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        parts = [p.strip() for p in res.stdout.split(",")]
        if len(parts) >= 5:
            temp, util, used_mb, free_mb, total_mb = parts[:5]
            print(f"\n{BOLD}NVIDIA System Stats (System Level):{RESET}")
            print(f"  - GPU Temperature: {temp}°C")
            print(f"  - GPU Utilization: {util}%")
            print(f"  - Memory Used:     {float(used_mb) / 1024:.2f} GB")
            print(f"  - Memory Free:     {float(free_mb) / 1024:.2f} GB")
            print(f"  - Memory Total:    {float(total_mb) / 1024:.2f} GB")
    except (subprocess.CalledProcessError, FileNotFoundError):
        log_warn("nvidia-smi not available or GPU driver not found.")

    # 3. Ollama Server & Resident Models
    diagnose_ollama()


def diagnose_ollama():
    print(f"\n{BOLD}Ollama Resident Model Check:{RESET}")
    # Load config host if possible, else check env
    ollama_host = (
        os.environ.get("OLLAMA_HOST")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )
    ollama_host = ollama_host.rstrip("/")

    # Check connection
    try:
        req = urllib.request.Request(f"{ollama_host}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            log_success(f"Ollama server is active at {ollama_host}")
            models = [m.get("name") for m in data.get("models", [])]
            print(f"  - Available local models: {', '.join(models) if models else 'None'}")
    except Exception as e:
        log_error(f"Cannot connect to Ollama server at {ollama_host}: {e}")
        return

    # Check active/resident models
    try:
        req = urllib.request.Request(f"{ollama_host}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            resident = data.get("models", [])
            if resident:
                log_warn(f"{len(resident)} model(s) currently resident in VRAM:")
                for m in resident:
                    name = m.get("name", "unknown")
                    size_gb = m.get("size", 0) / (1024**3)
                    vram_gb = m.get("size_vram", 0) / (1024**3)
                    print(
                        f"    * {BOLD}{name}{RESET} (Resident Size: {size_gb:.2f} GB, VRAM allocation: {vram_gb:.2f} GB)"
                    )
                print(
                    f"  {YELLOW}Note: Active text pipelines must evict these models (keep_alive=0) before GPU/SD phases to avoid OOM.{RESET}"
                )
            else:
                log_success("No models are currently resident in VRAM. GPU memory is clean.")
    except Exception as e:
        log_warn(f"Failed to retrieve active model list (/api/ps): {e}")


# ── Media File Diagnostics ─────────────────────────────────────────────────


def diagnose_media(file_path_str: str):
    file_path = Path(file_path_str)
    print(f"\n{BOLD}{CYAN}=== Media Diagnostics: {file_path.name} ==={RESET}")

    if not file_path.exists():
        log_error(f"File does not exist: {file_path}")
        return

    size_kb = file_path.stat().st_size / 1024
    log_info(f"File size: {size_kb:.2f} KB")

    suffix = file_path.suffix.lower()
    if suffix in (".wav", ".mp3", ".ogg", ".flac"):
        diagnose_audio(file_path)
    elif suffix in (".mp4", ".mkv", ".avi", ".mov"):
        diagnose_video(file_path)
    else:
        log_warn(
            f"Unsupported media format '{suffix}' for detailed probing. Attempting generic ffprobe..."
        )
        run_generic_probe(file_path)


def diagnose_audio(path: Path):
    if not check_ffmpeg():
        log_error("ffprobe/ffmpeg is not installed or available in the system PATH.")
        return

    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,bit_rate:stream=sample_rate,channels,codec_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)

        format_info = data.get("format", {})
        streams = data.get("streams", [])

        duration = float(format_info.get("duration", 0.0))
        int(format_info.get("bit_rate", 0)) if format_info.get("bit_rate") else 0

        log_success("Audio file probed successfully.")
        print(f"  - Duration:   {BOLD}{duration:.2f} seconds{RESET}")

        if streams:
            astream = streams[0]
            codec = astream.get("codec_name", "unknown")
            sample_rate = int(astream.get("sample_rate", 0))
            channels = int(astream.get("channels", 0))

            print(f"  - Codec:      {codec}")
            print(f"  - Sample Rate:{sample_rate} Hz")
            print(
                f"  - Channels:   {channels} ({'Mono' if channels == 1 else 'Stereo' if channels == 2 else 'Multichannel'})"
            )

            # Video.AI specific rules
            if sample_rate not in {44100, 24000}:
                log_warn(
                    f"Non-standard sample rate ({sample_rate}Hz). Active pipelines expect 24000Hz (raw OmniVoice) or 44100Hz (post-processed)."
                )
            else:
                log_success(f"Sample rate conforms to project standards ({sample_rate}Hz).")

            if channels > 1:
                log_info(
                    "Stereo audio detected. Voice cloning typically processes raw mono and upmixes later."
                )
        else:
            log_warn("No streams found in the audio file format container.")

    except Exception as e:
        log_error(f"Failed to probe audio file: {e}")


def diagnose_video(path: Path):
    if not check_ffmpeg():
        log_error("ffprobe/ffmpeg is not installed or available in the system PATH.")
        return

    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=width,height,avg_frame_rate,codec_name,codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)

        format_info = data.get("format", {})
        streams = data.get("streams", [])

        duration = float(format_info.get("duration", 0.0))
        log_success("Video file probed successfully.")
        print(f"  - Duration:   {BOLD}{duration:.2f} seconds{RESET}")

        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if vstream:
            w = int(vstream.get("width", 0))
            h = int(vstream.get("height", 0))
            codec = vstream.get("codec_name", "unknown")

            # Parse frame rate
            fps_str = vstream.get("avg_frame_rate", "0/0")
            fps = 0.0
            if "/" in fps_str:
                num, denom = map(float, fps_str.split("/"))
                if denom > 0:
                    fps = num / denom

            print(f"  - Resolution: {BOLD}{w}x{h}{RESET}")
            print(f"  - Video Codec:{codec}")
            print(f"  - Frame Rate: {fps:.2f} fps")

            # Check Video.AI standards
            if w == 1920 and h == 1080:
                log_success("Resolution matches final target output (1080p).")
            elif w == 768 and h == 432:
                log_info(
                    "Raw segment resolution detected (768x432 SD). Concat stage will upscale this to 1080p."
                )
            else:
                log_warn(f"Non-standard canvas resolution: {w}x{h}")

            if abs(fps - 24.0) > 0.1 and abs(fps - 12.0) > 0.1:
                log_warn(
                    f"Non-standard frame rate: {fps:.2f} fps. Rendering expects 24fps (classic) or 12fps (cheaper zoompan)."
                )
        else:
            log_error("No video stream found inside the file container.")

        if astream:
            print(f"  - Audio Track: Present ({astream.get('codec_name', 'unknown')})")
        else:
            log_warn("No audio track matched in the compiled video file.")

    except Exception as e:
        log_error(f"Failed to probe video file: {e}")


def run_generic_probe(path: Path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        print(res.stdout)
    except Exception as e:
        log_error(f"Generic probe failed: {e}")


# ── General System Sanity Check ─────────────────────────────────────────────


def diagnose_system():
    print(f"\n{BOLD}{CYAN}=== Environment & System Sanity Check ==={RESET}")

    # OS & Paths
    log_info(f"Operating System: {sys.platform}")
    log_info(f"Python Executable: {sys.executable}")
    log_info(f"Working Directory: {Path.cwd()}")

    # FFmpeg check
    if check_ffmpeg():
        log_success("FFmpeg and FFprobe are AVAILABLE in the system path.")
    else:
        log_error("FFmpeg and/or FFprobe are MISSING. Media rendering and diagnostics will fail.")

    # Project directories checks
    dirs = ["core", "config", "audio", "video", "utils", "studio_checkpoints", "studio_outputs"]
    print(f"\n{BOLD}Project Directory Diagnostics:{RESET}")
    for d in dirs:
        p = Path(d)
        if p.exists() and p.is_dir():
            log_success(f"  - {d}/ exists ({len(list(p.glob('*')))} items)")
        else:
            log_warn(f"  - {d}/ directory is missing or not initialized.")

    # Requirements check
    reqs = Path("requirements.txt")
    if reqs.exists():
        log_success("requirements.txt found.")
    else:
        log_warn("requirements.txt missing.")


# ── Main CLI Flow ──────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} [gpu | media <file_path> | system | all]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "gpu":
        diagnose_gpu()
    elif cmd == "media":
        if len(sys.argv) < 3:
            log_error("Please specify the path to a media file.")
            sys.exit(1)
        diagnose_media(sys.argv[2])
    elif cmd == "system":
        diagnose_system()
    elif cmd == "all":
        diagnose_system()
        diagnose_gpu()
    else:
        log_error(f"Unknown diagnostic command: {cmd}")
        print(f"Usage: python {sys.argv[0]} [gpu | media <file_path> | system | all]")
        sys.exit(1)


if __name__ == "__main__":
    main()
