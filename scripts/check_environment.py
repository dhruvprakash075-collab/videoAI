#!/usr/bin/env python3
"""
Environment validation script for Video.AI.

Checks:
- Python venv is active and correct version
- FFmpeg available on PATH
- Ollama running and reachable
- Director and writer models installed
- GPU VRAM available
- Disk space available
- Key directories exist
"""

import subprocess
import sys
from pathlib import Path

import requests


def check_python_venv() -> tuple[bool, str]:
    """Check if running inside correct venv."""
    if not hasattr(sys, "real_prefix") and not (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix):
        return False, "Not running inside a virtual environment"
    if sys.version_info < (3, 12):
        return False, f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.12"
    return True, f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def check_ffmpeg() -> tuple[bool, str]:
    """Check if FFmpeg is available on PATH."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Extract version line
            version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
            return True, version_line
        return False, "ffmpeg returned non-zero exit code"
    except FileNotFoundError:
        return False, "ffmpeg not found on PATH"
    except Exception as e:
        return False, str(e)


def check_ollama() -> tuple[bool, str]:
    """Check if Ollama server is reachable."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            return True, f"Ollama running, {len(models)} model(s) installed"
        return False, f"Ollama returned status {resp.status_code}"
    except requests.ConnectionError:
        return False, "Ollama not reachable at http://localhost:11434"
    except Exception as e:
        return False, str(e)


def check_ollama_models() -> tuple[bool, str]:
    """Check if required models are installed."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code != 200:
            return False, "Could not reach Ollama"
        data = resp.json()
        models = {m["name"].split(":")[0] for m in data.get("models", [])}
        required = {"hermes-director"}
        missing = required - models
        if missing:
            return False, f"Missing models: {', '.join(missing)}"
        return True, f"All required models present: {', '.join(sorted(models))}"
    except Exception as e:
        return False, str(e)


def check_gpu_vram() -> tuple[bool, str]:
    """Check GPU VRAM using nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                free, total = lines[0].split(",")
                free_gb = float(free) / 1024
                total_gb = float(total) / 1024
                threshold = 4.5
                status = "OK" if free_gb >= threshold else "LOW"
                return True, f"{free_gb:.1f}/{total_gb:.1f} GB free ({status})"
        return False, "nvidia-smi returned non-zero exit code"
    except FileNotFoundError:
        return False, "nvidia-smi not found (NVIDIA GPU not available)"
    except Exception as e:
        return False, str(e)


def check_disk_space(path: str = "studio_outputs", threshold_gb: int = 10) -> tuple[bool, str]:
    """Check disk space available."""
    try:
        p = Path(path).resolve()
        # Get free space on the same drive
        result = subprocess.run(
            ["powershell", "-Command", f"(Get-Volume -DriveLetter {p.drive[0]}).SizeRemaining / 1GB"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            free_gb = float(result.stdout.strip())
            status = "OK" if free_gb >= threshold_gb else "LOW"
            return True, f"{free_gb:.1f} GB free on {p.drive} ({status})"
        # Fallback: use os.statvfs if not on Windows
        import os
        stat_result = os.statvfs(str(p))
        free_gb = (stat_result.f_bavail * stat_result.f_frsize) / (1024**3)
        status = "OK" if free_gb >= threshold_gb else "LOW"
        return True, f"{free_gb:.1f} GB free ({status})"
    except Exception as e:
        return False, str(e)


def check_directories() -> tuple[bool, str]:
    """Check if key directories exist."""
    required = [
        "venv",
        "studio_outputs",
        "studio_projects",
        "character_voices",
        "logs",
        "config",
        "external/ComfyUI",
    ]
    missing = []
    for d in required:
        if not Path(d).exists():
            missing.append(d)
    if missing:
        return False, f"Missing directories: {', '.join(missing)}"
    return True, f"All {len(required)} required directories present"


def check_config_file() -> tuple[bool, str]:
    """Check if config file exists and is valid."""
    try:
        cfg_path = Path("config/config.yaml")
        if not cfg_path.exists():
            return False, "config/config.yaml not found"
        # Try to load it with UTF-8 encoding
        import yaml
        with open(cfg_path, encoding="utf-8", errors="replace") as f:
            yaml.safe_load(f)
        return True, "config/config.yaml valid"
    except Exception as e:
        return False, f"config/config.yaml error: {e}"


def main():
    """Run all checks and report."""
    checks = [
        ("Python venv", check_python_venv),
        ("FFmpeg", check_ffmpeg),
        ("Ollama server", check_ollama),
        ("Ollama models", check_ollama_models),
        ("GPU VRAM", check_gpu_vram),
        ("Disk space", check_disk_space),
        ("Required directories", check_directories),
        ("Config file", check_config_file),
    ]

    print("\n" + "=" * 70)
    print("Video.AI Environment Check".center(70))
    print("=" * 70 + "\n")

    results = []
    for name, check_fn in checks:
        try:
            ok, msg = check_fn()
            status = "✓ OK" if ok else "✗ FAIL"
            results.append((name, ok, msg))
            print(f"  {status:8} {name:25} {msg}")
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"  ✗ FAIL  {name:25} {e!s}")

    # Summary
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"Summary: {passed}/{total} checks passed")
    if passed == total:
        print("✓ Environment is ready for Video.AI pipeline execution".center(70))
        print("=" * 70 + "\n")
        return 0
    else:
        print("✗ Some checks failed. Fix issues above and re-run.".center(70))
        print("=" * 70 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
