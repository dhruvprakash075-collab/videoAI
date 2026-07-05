"""renderer.py — Hyperframes integration with assembler fallback."""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
_NPX = shutil.which("npx.cmd") or shutil.which("npx")

if not _NPX:
    log.warning("npx not found — Hyperframes render will be skipped")


def render_html(html_path, output_path, variables=None, quiet=True, duration=0.0):
    if not _NPX:
        raise RuntimeError("npx not found")

    # Derive WSL environment from env vars so this works on any machine (B19 fix).
    # Set VIDEOAI_WSL_DISTRO and VIDEOAI_WSL_USER in your environment to override.
    wsl_distro = os.environ.get("VIDEOAI_WSL_DISTRO", "Ubuntu")
    wsl_user = os.environ.get("VIDEOAI_WSL_USER", "")

    # Detect WSL availability before attempting
    try:
        _wsl_check = subprocess.run(["wsl", "--list", "--quiet"], capture_output=True, timeout=5)
        if _wsl_check.returncode != 0:
            raise RuntimeError("WSL not available or no distros installed")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"WSL unavailable: {e}") from e

    # Route through WSL — derive project path from cwd
    def _to_wsl(p):
        s = str(p).replace("\\", "/")
        if len(s) > 1 and s[1] == ":":
            drive = s[0].lower()
            return f"/mnt/{drive}{s[2:]}"
        return s

    wsl_dir = _to_wsl(html_path.parent)
    wsl_out = _to_wsl(output_path)
    wsl_project = _to_wsl(Path.cwd())

    os.environ.setdefault("WSL_WIN_PATH_MAP", "1")

    cmd = ["wsl", "-d", wsl_distro]
    if wsl_user:
        cmd += ["-u", wsl_user]
    cmd += [
        "--cd",
        wsl_project,
        "-e",
        "npx",
        "hyperframes",
        "render",
        wsl_dir,
        "--output",
        wsl_out,
        "--workers",
        "1",
        "--quality",
        "draft",
        "--fps",
        "24",
    ]
    if variables:
        cmd += ["--variables", json.dumps(variables)]
    if quiet:
        cmd.append("--quiet")

    log.info("Hyperframes (WSL distro=%s): %s -> %s", wsl_distro, html_path.name, output_path.name)
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,  # nosemgrep
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        hf_timeout = max(120, int(duration * 3))
        log.debug("Hyperframes timeout: %ds (duration=%.1fs)", hf_timeout, duration)
        _out, err = proc.communicate(timeout=hf_timeout)
        if proc.returncode != 0:
            raise RuntimeError("Hyperframes failed: " + err.strip()[-200:])
    except subprocess.TimeoutExpired as e:
        if proc is None:
            raise RuntimeError("Hyperframes timed out before process start") from e
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        raise RuntimeError("Hyperframes timed out") from e

    if not output_path.exists():
        raise RuntimeError("Output not created: " + str(output_path))
    log.info("Hyperframes complete: %s", output_path)
    return output_path


def build_html(
    audio_path,
    image_paths,
    script,
    duration,
    subtitle_script=None,
    transition="cross_fade",
    word_timestamps_json=None,
):
    """Build lint-clean Hyperframes composition.

    subtitle_script: the text to burn as captions (Devanagari when lang=hi).
                     Falls back to script if not provided (B1 fix).
    word_timestamps_json: optional Path to word-level timestamp JSON.  When
                          provided, caption data-start/data-duration values are
                          derived from real word timestamps instead of equal
                          duration slices (P4-6 fix).
    """
    # Use the subtitle-specific script for captions (B1 fix)
    caption_text = subtitle_script if subtitle_script else script

    n = len(image_paths) if image_paths else 1
    per = duration / n
    parts = [
        "<!doctype html>",
        '<html lang="hi"><head>',
        '<meta charset="UTF-8"><meta name="viewport" content="width=1920,height=1080">',
        # B7 fix: Noto Sans Devanagari for Hindi glyph support, with Latin fallbacks
        "<style>@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@600&display=swap');",
        "*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}",
        "html,body{width:1920px;height:1080px;overflow:hidden;background:#0a0a12}",
        ".clip{position:absolute;opacity:0;transition:opacity 0.8s ease-out}",
        "</style></head><body>",
    ]
    parts.append(
        '<div id="root" data-composition-id="main" data-width="1920" '
        f'data-height="1080" data-start="0" data-duration="{duration:.1f}">'
    )
    aname = audio_path.name if audio_path else "narration.wav"
    parts.append(
        f'<audio id="main-audio" src="assets/{aname}" data-start="0" '
        f'data-duration="{duration:.1f}" data-track-index="0" data-volume="1"></audio>'
    )
    for i, img in enumerate(image_paths):
        parts.append(
            f'<img id="scene-{i}" class="clip" src="assets/{Path(img).name}" '
            f'data-start="{i * per:.1f}" data-duration="{per:.1f}" '
            'style="width:102%;height:102%;top:-1%;left:0;object-fit:cover">'
        )

    # Split caption text into natural sentence chunks (B40: use caption_text not script)
    sentences = [
        s.strip() for s in re.split(r"(?<!\d)\.(?=\s|$)|[!?।]+", caption_text) if s.strip()
    ]
    if not sentences:
        sentences = [caption_text.strip()] if caption_text.strip() else []

    lines = []
    for s in sentences:
        words = s.split()
        if len(words) > 8:
            for i in range(0, len(words), 8):
                sub_line = " ".join(words[i : i + 8]).strip()
                if sub_line:
                    lines.append(sub_line)
        else:
            lines.append(s)

    # P4-6 fix: use real word timestamps when available so caption data-start /
    # data-duration values reflect actual audio timing instead of equal slices.
    # Fall back to equal-duration slices when no timestamps are provided.
    caption_timings = None  # list of (start, duration) tuples, one per line
    if word_timestamps_json is not None:
        _wts_path = Path(word_timestamps_json)
        if _wts_path.exists():
            try:
                import json as _json

                word_data = _json.loads(_wts_path.read_text(encoding="utf-8"))
                if word_data and lines:
                    # Map each caption line to the time span of its words.
                    # Strategy: distribute words across lines proportionally, then
                    # look up the first/last word timestamp for each line's span.
                    all_words = [w for w in word_data if w.get("word", "").strip()]
                    if all_words:
                        sum(len(l.split()) for l in lines)
                        timings = []
                        word_idx = 0
                        for line in lines:
                            n_words = len(line.split())
                            end_idx = min(word_idx + n_words, len(all_words)) - 1
                            t_start = all_words[word_idx].get("start", 0.0)
                            t_end = all_words[end_idx].get("end", t_start + 1.0)
                            timings.append((t_start, max(0.1, t_end - t_start)))
                            word_idx = min(word_idx + n_words, len(all_words) - 1)
                        caption_timings = timings
                        log.debug(
                            f"[build_html] Using word timestamps for {len(timings)} caption lines"
                        )
            except Exception as _wts_err:
                log.debug(f"[build_html] Could not use word timestamps: {_wts_err}")

    if caption_timings is None:
        sdur = duration / len(lines) if lines else duration
        caption_timings = [(i * sdur, sdur) for i in range(len(lines))]

    for i, (line, (t_start, t_dur)) in enumerate(zip(lines, caption_timings, strict=False)):
        # B7 fix: Noto Sans Devanagari font for Hindi glyph rendering
        parts.append(
            f'<div id="cap-{i}" class="clip" data-start="{t_start:.3f}" '
            f'data-duration="{t_dur:.3f}" style="bottom:120px;width:100%;text-align:center;'
            "font-family:'Noto Sans Devanagari','Noto Sans',sans-serif;font-size:42px;font-weight:600;"
            f'color:#fff;text-shadow:0 2px 12px rgba(0,0,0,0.8);z-index:10">{line}</div>'
        )
    parts.append("</div>")
    parts.append(
        "<script>window.__timelines=window.__timelines||{};"
        'window.__timelines["main"]={seek:function(){}, pause:function(){}, duration:function(){return '
        + str(duration)
        + ";}};</script>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def render_with_assets(
    compositions_dir,
    output_path,
    audio_path,
    image_paths,
    script,
    style="",
    html_content=None,
    transition="cross_fade",
    subtitle_script=None,
    word_timestamps_json=None,
    is_final=True,
    config=None,
):
    """Render segment — tries Hyperframes first, falls back to assembler.

    subtitle_script: text to burn as captions (Devanagari when lang=hi). B1 fix.
    word_timestamps_json: path to word-level timestamp JSON for synced subs. B2 fix.
    is_final: B5 — True for production renders (base whisper, CPU int8);
              False for preview/dry runs (tiny whisper).
    """
    compositions_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = compositions_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    if audio_path and audio_path.exists():
        shutil.copy2(audio_path, assets_dir / audio_path.name)
    for img in image_paths:
        p = Path(img)
        if p.exists():
            shutil.copy2(p, assets_dir / p.name)

    # ── Render path selection ─────────────────────────────────────────────
    # The FFmpeg assembler is the proven, fast, offline-pure path on Windows.
    # Hyperframes (WSL + npx + headless Chrome) is fragile: on a Windows box it
    # commonly hangs for duration*3 seconds before timing out and falling back,
    # wasting up to ~17 min per segment (observed in real runs). It is therefore
    # OPT-IN: set VIDEOAI_USE_HYPERFRAMES=1 to try it. Default = assembler only.
    _use_hyperframes = os.environ.get("VIDEOAI_USE_HYPERFRAMES", "0") == "1"

    if _use_hyperframes:
        try:
            from utils import get_audio_duration

            dur = get_audio_duration(audio_path) if audio_path else len(image_paths) * 5.0
            if html_content:
                html_text = html_content
            else:
                html_text = build_html(
                    audio_path,
                    image_paths,
                    script,
                    dur,
                    subtitle_script=subtitle_script,
                    transition=transition,
                    word_timestamps_json=word_timestamps_json,
                )
            html_path = compositions_dir / "index.html"
            html_path.write_text(html_text, encoding="utf-8")
            return render_html(html_path, output_path, duration=dur)
        except Exception as hf_err:
            log.warning("Hyperframes failed (%s) -> falling back to assembler", hf_err)
            try:
                from agents.director_agent import UIState

                seg_match_deg = re.search(r"segment_(\d+)", str(output_path))
                _seg_num_deg = int(seg_match_deg.group(1)) if seg_match_deg else 0
                UIState.add_degradation(
                    _seg_num_deg, "hyperframes_fallback", f"Hyperframes failed: {str(hf_err)[:100]}"
                )
            except Exception as _deg_err:
                log.debug(f"[renderer] Could not record Hyperframes degradation: {_deg_err}")

    # Default / fallback: FFmpeg assembler (pass subtitle_script + word_timestamps_json, B1/B2 fix)
    from video.renderer.assembler import create_segment_mp4

    seg_match = re.search(r"segment_(\d+)", str(output_path))
    seg_num = int(seg_match.group(1)) if seg_match else 1
    if config is None:
        from config import load_config
        config = load_config()
    return create_segment_mp4(
        seg_num,
        audio_path,
        subtitle_script if subtitle_script else script,
        output_path.parent,
        config,
        images=image_paths or [],
        word_timestamps_json=word_timestamps_json,
        is_final=is_final,
    )
