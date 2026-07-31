"""Pre-flight health checks before pipeline start."""
from __future__ import annotations

import json as _json
import logging
import shutil
import sys
import urllib.request
from pathlib import Path

from utils.preflight import PreflightCheck, PreflightResult, _timed
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

    result = PreflightResult()

    def _check_ffmpeg():
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ("ok", ffmpeg_path)
        return ("fail", "NOT FOUND on PATH!")

    def _check_omnivoice_python():
        omnivoice_path = Path("omnivoice_env/Scripts/python.exe")
        if omnivoice_path.exists():
            return ("ok", str(omnivoice_path.resolve()))
        return ("ok", f"Using system Python: {sys.executable}")

    def _check_tts_engine():
        _KNOWN_TTS_ENGINES = {"indicf5", "supertonic", "omnivoice"}
        tts_engine = config.get("tts", {}).get("engine", "indicf5")
        if tts_engine not in _KNOWN_TTS_ENGINES:
            return (
                "fail",
                f"Unknown engine '{tts_engine}'. Supported: {', '.join(sorted(_KNOWN_TTS_ENGINES))}",
            )
        if tts_engine == "indicf5":
            indic_root = Path(config.get("tts", {}).get("indicf5", {}).get("root", r"D:\IndicF5"))
            if indic_root.exists():
                return ("ok", f"IndicF5 checkout available: {indic_root}")
            return ("fail", f"IndicF5 checkout NOT FOUND: {indic_root} (will fall back)")
        if tts_engine == "supertonic":
            worker = Path("audio/supertonic_worker.py")
            if worker.exists():
                return ("ok", "Supertonic worker script available")
            return ("fail", "audio/supertonic_worker.py NOT FOUND!")
        if tts_engine == "omnivoice":
            worker = Path("audio/omnivoice_worker.py")
            if worker.exists():
                return ("ok", "OmniVoice worker script available")
            return ("fail", "audio/omnivoice_worker.py NOT FOUND!")
        return ("ok", "")

    def _check_disk():
        try:
            _total, _used, free = shutil.disk_usage(".")
            free_gb = free / (1024**3)
            if free_gb > 10.0:
                return ("ok", f"{free_gb:.1f} GB free")
            return ("fail", f"Only {free_gb:.1f} GB free (10GB recommended)")
        except Exception as e:
            return ("fail", f"Check failed: {e}")

    def _check_ollama_connection():
        try:
            tags_url = build_validated_url(validate_service_base_url(ollama_host), "/api/tags")
            req = urllib.request.Request(tags_url, headers={"User-Agent": "Video.AI Preflight"})
            with open_validated_url(req, timeout=3) as response:
                data = _json.loads(response.read().decode("utf-8"))
                tags = [t["name"] for t in data.get("models", [])]
                return ("ok", f"Connected to {ollama_host}"), tags
        except Exception as e:
            return ("fail", f"Cannot connect: {e}"), []

    def _check_director_model(tags):
        found = any(director_model in t or t.startswith(director_model) for t in tags)
        if found:
            return ("ok", "Available in Ollama")
        return ("fail", f"Model '{director_model}' not loaded in Ollama!")

    def _check_writer_model(tags):
        found = any(writer_model in t or t.startswith(writer_model) for t in tags)
        if found:
            return ("ok", "Available in Ollama")
        return ("warn", f"Model '{writer_model}' not pulled yet — run: ollama pull {writer_model}")

    result.checks.append(_timed(_check_ffmpeg, "FFmpeg Executable on PATH"))
    result.checks.append(_timed(_check_omnivoice_python, "OmniVoice Python Environment"))
    result.checks.append(_timed(_check_tts_engine, f"TTS Engine '{config.get('tts', {}).get('engine', 'indicf5')}'"))
    result.checks.append(_timed(_check_disk, "Disk Space Availability"))

    ollama_status, ollama_tags = _check_ollama_connection()
    result.checks.append(
        PreflightCheck(name="Ollama Endpoint Connection", status=ollama_status[0], message=ollama_status[1])
    )

    if ollama_status[0] == "ok":
        result.checks.append(
            _timed(lambda: _check_director_model(ollama_tags), f"Ollama Model '{director_model}'")
        )
        result.checks.append(
            _timed(lambda: _check_writer_model(ollama_tags), f"Ollama Model '{writer_model}'")
        )
    else:
        result.checks.append(
            PreflightCheck(name=f"Ollama Model '{director_model}'", status="fail", message="Ollama connection failed")
        )
        result.checks.append(
            PreflightCheck(name=f"Ollama Model '{writer_model}'", status="fail", message="Ollama connection failed")
        )

    log.info(f"{'Check Name':<35} | {'Status':<8} | Details")
    log.info("-" * 80)
    for check in result.checks:
        if check.status == "ok":
            symbol = "[OK]"
        elif check.status == "warn":
            symbol = "[WARN]"
        else:
            symbol = "[FAILED]"
        log.info(f"{check.name:<35} | {symbol:<8} | {check.message}")
    log.info("=" * 80)

    if result.failures:
        log.warning("WARNING: Some preflight system health checks failed. Run may fail!")
        if not dry_run:
            ffmpeg_failed = any(
                c.name == "FFmpeg Executable on PATH" and c.status == "fail" for c in result.failures
            )
            if ffmpeg_failed:
                raise RuntimeError(
                    "Fatal: FFmpeg is missing from PATH. Video generation is impossible."
                )
