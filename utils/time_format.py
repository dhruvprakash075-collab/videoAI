"""Time / duration formatting shared by pre_production and post_production."""
from __future__ import annotations

from pathlib import Path


def format_time_hms(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_chapters_time(sec: float) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_video_duration(mp4: Path) -> float:
    """Read a video's actual duration via ffprobe. Returns 30.0 on error."""
    import json as _json
    import subprocess

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(mp4)],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        )
        return float(_json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 30.0
