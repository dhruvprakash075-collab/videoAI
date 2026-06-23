"""preflight.py — Pipeline readiness checks.

Run these before kicking off a long generation to surface obvious failures
fast: Ollama down, VRAM exhausted, disk full, ffmpeg missing, model files
gone. The pipeline itself degrades gracefully but a 30-minute run failing at
minute 28 is expensive; a 30-second preflight is cheap.

Usage:
    from utils.preflight import run_preflight
    result = run_preflight()                # full check, exit(1) on any fail
    if not result.all_ok:
        sys.exit(1)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class PreflightCheck:
    name: str
    status: Status
    message: str = ""
    duration_ms: int = 0

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_fail(self) -> bool:
        return self.status == "fail"


@dataclass
class PreflightResult:
    checks: list[PreflightCheck] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[PreflightCheck]:
        return [c for c in self.checks if c.status == "warn"]

    @property
    def all_ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        ok = sum(1 for c in self.checks if c.status == "ok")
        warn = len(self.warnings)
        fail = len(self.failures)
        skip = sum(1 for c in self.checks if c.status == "skip")
        return f"ok={ok} warn={warn} fail={fail} skip={skip}"


def _timed(fn: Callable[[], tuple[Status, str]], name: str | None = None) -> PreflightCheck:
    """Run a check function and capture duration. fn returns (status, message).

    `name` overrides the function name (useful for lambdas).
    """
    t0 = time.perf_counter()
    try:
        status, message = fn()
    except Exception as e:
        status, message = "fail", f"check raised: {e}"
    dt_ms = int((time.perf_counter() - t0) * 1000)
    return PreflightCheck(
        name=name or fn.__name__,
        status=status,
        message=message,
        duration_ms=dt_ms,
    )


def _check_ollama(config: dict) -> tuple[Status, str]:
    """Ping Ollama /api/tags with a 3s timeout. If reachable, report model count."""
    import urllib.error
    import urllib.request

    from utils.url_security import validate_service_base_url

    host = config.get("ollama", {}).get("host", "http://localhost:11434")
    try:
        validated = validate_service_base_url(host)
    except ValueError as e:
        return "fail", f"Ollama host validation failed: {e}"
    url = f"{validated.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Video.AI-Preflight"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = data.get("models", [])
            return "ok", f"{host} reachable, {len(models)} model(s) installed"
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return "fail", f"Cannot reach Ollama at {host}: {e}"
    except Exception as e:
        return "fail", f"Ollama probe failed: {e}"


def _check_director_model(config: dict) -> tuple[Status, str]:
    """Verify the configured director model is installed in Ollama."""
    import urllib.error
    import urllib.request

    from utils.url_security import validate_service_base_url

    host = config.get("ollama", {}).get("host", "http://localhost:11434")
    director = config.get("models", {}).get("director", "hermes-director")
    try:
        validated = validate_service_base_url(host)
    except ValueError as e:
        return "warn", f"Ollama host validation failed: {e}"
    url = f"{validated.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            names = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
            base = director.split(":")[0]
            if base in names:
                return "ok", f"director model '{director}' installed"
            return "fail", (
                f"director model '{director}' not found in Ollama. Run: ollama pull {director}"
            )
    except Exception as e:
        return "warn", f"could not verify director model: {e}"


def _check_vram(config: dict) -> tuple[Status, str]:
    """Check that free VRAM is above the SD threshold (default 4.5 GB)."""
    try:
        import torch
    except ImportError:
        return "skip", "torch not installed (CPU-only build)"

    if not torch.cuda.is_available():
        return "skip", "no CUDA GPU detected"

    threshold = float(config.get("performance", {}).get("vram_sd_threshold_gb", 4.5))
    free_gb, total_gb = (x / (1024**3) for x in torch.cuda.mem_get_info(0))
    if free_gb >= threshold:
        return "ok", f"{free_gb:.1f}/{total_gb:.1f} GB free (threshold {threshold:.1f} GB)"
    return "fail", (
        f"only {free_gb:.1f}/{total_gb:.1f} GB free; need {threshold:.1f} GB for Stable Diffusion. "
        f"Try: ollama stop <model>, or close other GPU apps."
    )


def _check_disk(config: dict) -> tuple[Status, str]:
    """Verify at least 5 GB free on the studio_outputs volume."""
    import psutil

    output_path = config.get("video", {}).get("output_path", "studio_outputs/final_video.mp4")
    target_dir = Path(output_path).parent.resolve()
    if not target_dir.exists():
        target_dir = target_dir.parent
    free_gb = psutil.disk_usage(str(target_dir)).free / (1024**3)
    if free_gb >= 5.0:
        return "ok", f"{free_gb:.1f} GB free on {target_dir}"
    if free_gb >= 1.0:
        return "warn", f"only {free_gb:.1f} GB free on {target_dir} (a 10-min video needs ~2-3 GB)"
    return "fail", f"only {free_gb:.1f} GB free on {target_dir}; clear space before running"


def _check_supertonic_voice(config: dict) -> tuple[Status, str]:
    """Verify the configured Supertonic voice JSON file exists on disk."""
    tts = config.get("tts", {})
    if tts.get("engine") != "supertonic":
        return "skip", "TTS engine is not supertonic"
    voice_path = tts.get("supertonic", {}).get("voice", "")
    if not voice_path:
        return "skip", "no supertonic.voice configured"
    p = Path(voice_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists():
        return "ok", f"supertonic voice JSON found: {p} ({p.stat().st_size / 1024:.0f} KB)"
    return "fail", f"supertonic voice JSON not found: {p}"



def _check_qwen_edit(config: dict) -> tuple[Status, str]:
    """Check Qwen-Image-Edit only when the optional mode is enabled."""
    img = config.get("image_gen", {}) or {}
    composition_mode = img.get("composition_mode", "one_pass")
    qwen = img.get("qwen_edit", {}) or {}
    if composition_mode != "qwen_edit" or not qwen.get("enabled", False):
        return "skip", "Qwen edit disabled"

    try:
        from video.image_gen.qwen_repose import preflight_qwen_edit
    except Exception as e:
        return "fail", f"could not import Qwen edit preflight: {e}"

    missing = preflight_qwen_edit(config)
    if missing:
        return (
            "warn",
            f"qwen_edit preflight found {len(missing)} issue(s); frames will fall back to base images. Issues:\n"
            + "\n".join(f"  - {item}" for item in missing),
        )
    return "ok", "qwen_edit preflight passed (workflow, model path, LoRA, and custom nodes present)"


def _check_playwright(config: dict) -> tuple[Status, str]:
    """When upload is enabled, verify Playwright browser is installed."""
    upload = config.get("upload", {})
    if not upload.get("enabled", False):
        return "skip", "Upload not enabled — skipping Playwright check"
    if upload.get("platform") != "youtube":
        return "skip", "Non-YouTube upload — skipping Playwright check"

    import sys

    _fix_cmd = f"{sys.executable} -m playwright install chromium"

    def _try_playwright(args: list[str]) -> int | None:
        """Try `python -m playwright ...` first, then bare `playwright`."""
        for cmd in ([sys.executable, "-m", "playwright", *args], ["playwright", *args]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                return r.returncode
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                return None
        return None

    try:
        rc = _try_playwright(["install", "--dry-run"])
        if rc == 0:
            return "ok", "Playwright browsers found"
        rc_chromium = _try_playwright(["install", "--dry-run", "chromium"])
        if rc_chromium == 0:
            return "ok", "Playwright Chromium found"
        if rc is None and rc_chromium is None:
            return (
                "fail",
                f"Playwright not found via `python -m playwright` or bare `playwright`. Fix: {_fix_cmd}",
            )
        return "fail", f"Playwright browser not installed. Fix: {_fix_cmd}"
    except Exception as e:
        return "warn", f"Playwright check failed: {e}"


def _check_ffmpeg() -> tuple[Status, str]:
    """Verify ffmpeg is in PATH and reports a version."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "fail", "ffmpeg not found in PATH (bootstrap should add the bundled binary)"
    try:
        out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
        first_line = out.stdout.splitlines()[0] if out.stdout else "(no output)"
        return "ok", f"{ffmpeg} - {first_line}"
    except subprocess.TimeoutExpired:
        return "warn", f"ffmpeg at {ffmpeg} hung (version probe timed out)"
    except Exception as e:
        return "warn", f"ffmpeg at {ffmpeg} failed: {e}"


def _check_python() -> tuple[Status, str]:
    """Verify Python version is in the supported range (3.10-3.14)."""
    import sys

    v = sys.version_info
    if v.major != 3 or v.minor < 10 or v.minor > 14:
        return "fail", f"Python {v.major}.{v.minor}.{v.micro} not supported (need 3.10-3.14)"
    return "ok", f"Python {v.major}.{v.minor}.{v.micro}"


def run_preflight(
    config: dict | None = None,
    *,
    fail_fast: bool = False,
    quiet: bool = False,
) -> PreflightResult:
    """Run all preflight checks. Returns a PreflightResult.

    Args:
        config: pipeline config dict (from config.load_config()). If None,
            checks that need config are skipped with a 'skip' status.
        fail_fast: raise on the first failed check (default: collect all).
        quiet: don't print the summary table.
    """
    if config is None:
        config = {}

    result = PreflightResult()
    checks: list[PreflightCheck] = [
        _timed(_check_python, name="python"),
        _timed(lambda: _check_ollama(config), name="ollama_ping"),
        _timed(lambda: _check_director_model(config), name="director_model"),
        _timed(lambda: _check_vram(config), name="vram"),
        _timed(lambda: _check_disk(config), name="disk_space"),
        _timed(lambda: _check_supertonic_voice(config), name="supertonic_voice"),
        _timed(lambda: _check_qwen_edit(config), name="qwen_edit"),
        _timed(_check_ffmpeg, name="ffmpeg"),
        _timed(lambda: _check_playwright(config), name="playwright"),
    ]
    for c in checks:
        result.checks.append(c)
        if fail_fast and c.is_fail:
            break

    if not quiet:
        print(_format_report(result))
    return result


def _format_report(result: PreflightResult) -> str:
    icon = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}
    lines = ["", "Preflight summary", "-" * 60]
    for c in result.checks:
        marker = icon.get(c.status, "?????")
        msg = c.message.replace("\n", " ")
        lines.append(f"  {marker} {c.name:<22} {msg}  ({c.duration_ms} ms)")
    lines.append("-" * 60)
    lines.append(f"  {result.summary()}")
    lines.append("")
    if result.failures:
        lines.append("FAILED -- fix the issues above before running the pipeline.")
    elif result.warnings:
        lines.append("OK with warnings -- pipeline will run but may hit issues.")
    else:
        lines.append("OK -- pipeline is ready.")
    return "\n".join(lines)


def main() -> int:
    """CLI entrypoint: `python -m utils.preflight`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from config import load_config

        config = load_config()
    except Exception as e:
        log.warning("Could not load config: %s — using empty config", e)
        config = {}

    result = run_preflight(config=config, fail_fast=False)
    return 0 if result.all_ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
