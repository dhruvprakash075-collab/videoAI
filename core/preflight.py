"""Pre-flight health checks before pipeline start."""
from __future__ import annotations

import json as _json
import logging
import shutil
import sys
import urllib.request
from pathlib import Path

from utils.url_security import build_validated_url, open_validated_url, validate_service_base_url

log = logging.getLogger(__name__)


def run_preflight_checks(config: dict, dry_run: bool = False) -> None:
    """Run startup checks to ensure all requirements are met before starting the long pipeline."""
    log.info("=" * 60)
    log.info("         RUNNING PRE-FLIGHT SYSTEM HEALTH CHECKS")
    log.info("=" * 60)

    ollama_host = config.get("ollama", {}).get("host", "http://localhost:11434")
    director_model = config.get("models", {}).get("director", "hermes-director")
    writer_model = config.get("models", {}).get("writer", "zephyr-writer")

    checks: dict[str, dict[str, str]] = {
        "Ollama Endpoint Connection": {"status": "PENDING", "info": ollama_host},
        f"Ollama Model '{director_model}'": {"status": "PENDING", "info": "Required for outlining"},
        f"Ollama Model '{writer_model}'": {"status": "PENDING", "info": "Required for scripting"},
        "FFmpeg Executable on PATH": {"status": "PENDING", "info": ""},
        "OmniVoice Python Environment": {
            "status": "PENDING",
            "info": "omnivoice_env/Scripts/python.exe",
        },
    }

    # 1. FFmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        checks["FFmpeg Executable on PATH"]["status"] = "OK"
        checks["FFmpeg Executable on PATH"]["info"] = ffmpeg_path
    else:
        checks["FFmpeg Executable on PATH"]["status"] = "FAILED"
        checks["FFmpeg Executable on PATH"]["info"] = "NOT FOUND on PATH!"

    # 2. OmniVoice Python
    omnivoice_python = Path("omnivoice_env/Scripts/python.exe")
    if omnivoice_python.exists():
        checks["OmniVoice Python Environment"]["status"] = "OK"
        checks["OmniVoice Python Environment"]["info"] = str(omnivoice_python.resolve())
    else:
        checks["OmniVoice Python Environment"]["status"] = "OK"
        checks["OmniVoice Python Environment"]["info"] = f"Using system Python: {sys.executable}"

    # 2.7 TTS engine
    _KNOWN_TTS_ENGINES = {"supertonic", "omnivoice"}
    tts_engine = config.get("tts", {}).get("engine", "supertonic")
    checks[f"TTS Engine '{tts_engine}'"] = {"status": "PENDING", "info": ""}
    if tts_engine not in _KNOWN_TTS_ENGINES:
        checks[f"TTS Engine '{tts_engine}'"]["status"] = "FAILED"
        checks[f"TTS Engine '{tts_engine}'"]["info"] = (
            f"Unknown engine '{tts_engine}'. Supported: {', '.join(sorted(_KNOWN_TTS_ENGINES))}"
        )
    elif tts_engine == "supertonic":
        worker_script = Path("audio/supertonic_worker.py")
        if worker_script.exists():
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "OK"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "Supertonic worker script available"
        else:
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "FAILED"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "audio/supertonic_worker.py NOT FOUND!"
    elif tts_engine == "omnivoice":
        worker_script = Path("audio/omnivoice_worker.py")
        if worker_script.exists():
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "OK"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "OmniVoice worker script available"
        else:
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "FAILED"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "audio/omnivoice_worker.py NOT FOUND!"

    # 2.5 Disk space
    checks.setdefault("Disk Space Availability", {})
    try:
        _total, _used, free = shutil.disk_usage(".")
        free_gb = free / (1024**3)
        if free_gb > 10.0:
            checks["Disk Space Availability"]["status"] = "OK"
            checks["Disk Space Availability"]["info"] = f"{free_gb:.1f} GB free"
        else:
            checks["Disk Space Availability"]["status"] = "FAILED"
            checks["Disk Space Availability"]["info"] = (
                f"Only {free_gb:.1f} GB free (10GB recommended)"
            )
    except Exception as e:
        checks["Disk Space Availability"]["status"] = "FAILED"
        checks["Disk Space Availability"]["info"] = f"Check failed: {e}"

    # 3. Ollama
    try:
        tags_url = build_validated_url(validate_service_base_url(ollama_host), "/api/tags")
        req = urllib.request.Request(
            tags_url,
            headers={"User-Agent": "Video.AI Preflight"},
        )
        with open_validated_url(req, timeout=3) as response:
            data = _json.loads(response.read().decode("utf-8"))
            checks["Ollama Endpoint Connection"]["status"] = "OK"
            checks["Ollama Endpoint Connection"]["info"] = f"Connected to {ollama_host}"
            tags = [t["name"] for t in data.get("models", [])]
            found_dir = any(director_model in t or t.startswith(director_model) for t in tags)
            if found_dir:
                checks[f"Ollama Model '{director_model}'"]["status"] = "OK"
                checks[f"Ollama Model '{director_model}'"]["info"] = "Available in Ollama"
            else:
                checks[f"Ollama Model '{director_model}'"]["status"] = "FAILED"
                checks[f"Ollama Model '{director_model}'"]["info"] = (
                    f"Model '{director_model}' not loaded in Ollama!"
                )
            found_writer = any(writer_model in t or t.startswith(writer_model) for t in tags)
            if found_writer:
                checks[f"Ollama Model '{writer_model}'"]["status"] = "OK"
                checks[f"Ollama Model '{writer_model}'"]["info"] = "Available in Ollama"
            else:
                checks[f"Ollama Model '{writer_model}'"]["status"] = "WARN"
                checks[f"Ollama Model '{writer_model}'"]["info"] = (
                    f"Model '{writer_model}' not pulled yet — run: ollama pull {writer_model}"
                )
    except Exception as e:
        checks["Ollama Endpoint Connection"]["status"] = "FAILED"
        checks["Ollama Endpoint Connection"]["info"] = f"Cannot connect: {e}"
        checks[f"Ollama Model '{director_model}'"]["status"] = "FAILED"
        checks[f"Ollama Model '{director_model}'"]["info"] = "Ollama connection failed"
        checks[f"Ollama Model '{writer_model}'"]["status"] = "FAILED"
        checks[f"Ollama Model '{writer_model}'"]["info"] = "Ollama connection failed"

    log.info(f"{'Check Name':<35} | {'Status':<8} | Details")
    log.info("-" * 80)
    failed = False
    for name, result in checks.items():
        if result["status"] == "OK":
            status_symbol = "[OK]"
        elif result["status"] == "WARN":
            status_symbol = "[WARN]"
        else:
            status_symbol = "[FAILED]"
            failed = True
        log.info(f"{name:<35} | {status_symbol:<8} | {result['info']}")
    log.info("=" * 80)

    if failed:
        log.warning("WARNING: Some preflight system health checks failed. Run may fail!")
        if checks["FFmpeg Executable on PATH"]["status"] == "FAILED" and not dry_run:
            raise RuntimeError(
                "Fatal: FFmpeg is missing from PATH. Video generation is impossible."
            )
