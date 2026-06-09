"""quality_check.py - Post-production video quality validation."""

import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def check_video(
    video_path: Path,
    config: dict,
    expected_duration_s: float | None = None,
    requested_duration_s: float | None = None,
) -> dict:
    """Run quality checks on the final video.

    Checks:
    1. File exists and non-empty
    2. Duration matches expected (within tolerance)
    3. Resolution matches config
    4. Audio stream present
    5. No encoding errors

    Args:
        video_path: Path to the final video file.
        config: Pipeline config dict.
        expected_duration_s: When provided, use this as the expected duration
            instead of ``total_duration_min * 60``.  Pass the sum of actual TTS
            segment durations so the check reflects real output length rather
            than the config target (P3-10 fix).
        requested_duration_s: When the user supplied a hard ``--duration`` target,
            pass it here so the quality check validates the output against the
            requested contract rather than the sum of segment durations.

    Returns {"passed": bool, "issues": List[str], "details": Dict}
    """
    issues: list[str] = []
    details: dict = {}

    if not video_path.exists():
        return {"passed": False, "issues": ["File not found"], "details": {}}

    size_mb = video_path.stat().st_size / (1024 * 1024)
    details["size_mb"] = round(size_mb, 2)

    if size_mb < 0.1:
        issues.append(f"File too small: {size_mb:.2f}MB")

    # 2. Probe with ffprobe
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if result.returncode != 0:
            issues.append(f"ffprobe error: {result.stderr[:100]}")
            return {"passed": False, "issues": issues, "details": details}
        probe = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        issues.append("ffprobe timeout (>30s)")
        return {"passed": False, "issues": issues, "details": details}
    except json.JSONDecodeError:
        issues.append("ffprobe output invalid JSON")
        return {"passed": False, "issues": issues, "details": details}
    except Exception as e:
        issues.append(f"ffprobe failed: {e}")
        return {"passed": False, "issues": issues, "details": details}

    # Duration check
    fmt = probe.get("format", {})
    # P3-11 fix: ffprobe emits "duration": "N/A" for some containers.
    # Wrap the conversion in try/except so a bad value records an issue
    # instead of raising ValueError at the end of a long run.
    try:
        duration = float(fmt.get("duration", 0) or 0)
    except (ValueError, TypeError):
        duration = 0.0
        issues.append("Could not read video duration (ffprobe returned N/A or invalid value)")
    details["duration_s"] = round(duration, 2)

    # When the user supplied a hard ``--duration`` target, use it as the
    # primary comparison so a 30s request cannot pass with a 275s output.
    if requested_duration_s is not None and requested_duration_s > 0:
        expected_s = requested_duration_s
        log.debug(f"QC: using requested_duration_s={expected_s:.1f}s (user target)")
    elif expected_duration_s is not None and expected_duration_s > 0:
        expected_s = expected_duration_s
        log.debug(f"QC: using caller-supplied expected_duration_s={expected_s:.1f}s")
    else:
        expected_min = config.get("video", {}).get("total_duration_min", 10)
        expected_s = expected_min * 60
    tolerance = expected_s * 0.2  # 20% tolerance

    if duration > 0 and abs(duration - expected_s) > tolerance:
        issues.append(f"Duration mismatch: {duration:.0f}s vs expected {expected_s:.0f}s")

    # Stream checks
    streams = probe.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not has_video:
        issues.append("No video stream found")
    if not has_audio:
        issues.append("No audio stream found")

    # Resolution check
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if video_streams:
        vs = video_streams[0]
        details["width"] = vs.get("width")
        details["height"] = vs.get("height")
        expected_res = config.get("video", {}).get("resolution", "1920x1080")
        if not re.match(r"^\d+x\d+$", expected_res):
            issues.append(f"Invalid resolution format: {expected_res}")
        else:
            exp_w, exp_h = map(int, expected_res.split("x"))
            if vs.get("width") != exp_w or vs.get("height") != exp_h:
                issues.append(
                    f"Resolution: {vs.get('width')}x{vs.get('height')} vs expected {exp_w}x{exp_h}"
                )

    log.info(f"Quality check: {'PASS' if not issues else 'FAIL'}")
    return {"passed": len(issues) == 0, "issues": issues, "details": details}
