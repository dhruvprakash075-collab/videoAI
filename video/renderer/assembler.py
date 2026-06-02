"""assembler.py - Phase 1: black-frame MP4. Phase 2: image slideshow with Ken Burns. Final concat."""

import logging
import math
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

# Whisper model cache to avoid reloading per segment
_whisper_model = None
_whisper_model_lock = threading.Lock()
_whisper_backend = None  # "faster" or "openai"

# Thread lock to serialize access to the shared cleanup manifest JSON file
_manifest_lock = threading.Lock()

def _get_whisper_model(is_final: bool = False):
    """Load whisper model. Prefers faster-whisper (CTranslate2, 4-8x faster), falls back to openai-whisper.

    B5: For final (non-preview/non-dry) renders, use performance.whisper_model_final
    (default "base") pinned to CPU int8 so it never competes with SD for VRAM.
    For preview/dry runs, use performance.whisper_model (default "tiny").
    """
    global _whisper_model, _whisper_backend
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_model_lock:
        if _whisper_model is None:
            try:
                from config import load_config
                cfg = load_config()
                perf = cfg.get("performance", {})
                if is_final:
                    model_name = perf.get("whisper_model_final", "base")
                    # B5: pin to CPU int8 so it never sits in VRAM during SD
                    _device = "cpu"
                    _compute = "int8"
                else:
                    model_name = perf.get("whisper_model", "tiny")
                    import torch as _torch
                    _device = "cuda" if _torch.cuda.is_available() else "cpu"
                    _compute = "float16" if _device == "cuda" else "int8"
            except Exception:
                model_name = "tiny"
                _device = "cpu"
                _compute = "int8"

            # Try faster-whisper first (CTranslate2 — 4-8x faster, GPU FP16)
            try:
                from faster_whisper import WhisperModel as FasterWhisperModel
                _whisper_model = FasterWhisperModel(model_name, device=_device, compute_type=_compute)
                _whisper_backend = "faster"
                log.info(f"Whisper: faster-whisper ({model_name}, {_device}, {_compute})")
            except Exception as e:
                log.warning(f"faster-whisper failed ({e}), falling back to openai-whisper")
                try:
                    import whisper
                    _whisper_model = whisper.load_model(model_name)
                    _whisper_backend = "openai"
                    log.info(f"Whisper: openai-whisper ({model_name})")
                except Exception as e2:
                    log.exception(f"Both whisper backends failed: {e2}")
                    return None
    return _whisper_model


import contextlib

from utils import get_audio_duration

log = logging.getLogger(__name__)

# Dedicated lock for Whisper transcription to prevent CPU/RAM OOM during parallel segment execution
_whisper_lock = threading.Lock()


_cached_codec = None

def _get_video_codec() -> list:
    """Return GPU encoder if available, fall back to CPU libx264. Cached after first call."""
    global _cached_codec
    if _cached_codec is not None:
        return _cached_codec
    if sys.platform == "win32":
        try:
            # Check for NVIDIA NVENC
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=5
            )
            if "h264_nvenc" in result.stdout:
                log.debug("Hardware acceleration: h264_nvenc detected")
                _cached_codec = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19",
                                 "-spatial-aq", "1", "-temporal-aq", "1", "-pix_fmt", "yuv420p"]
                return _cached_codec
            log.warning("h264_nvenc not found -- falling back to libx264")
        except Exception:
            log.warning("FFmpeg check failed -- falling back to libx264")
    _cached_codec = ["-threads", "0", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p"]
    return _cached_codec


def _encoder_args(config: dict) -> list:
    """Return encoder-specific FFmpeg arguments using config.yaml settings.

    Ada NVENC (RTX 4050) quality flags from config.video.encoder_extra:
      -spatial-aq 1   better quality in flat areas (~5% speed cost)
      -temporal-aq 1  better quality across frames (~5% speed cost)
      -b_ref_mode 1   middle B-frame as reference (better compression)
      -bf 3           3 B-frames (better compression ratio)
    """
    enc = config.get("video", {}).get("encoder", "h264_nvenc")
    preset = config.get("video", {}).get("encoder_preset", "p5")
    bitrate = config.get("video", {}).get("video_bitrate", "8M")
    if enc == "h264_nvenc":
        args = ["-c:v", "h264_nvenc", "-preset", preset, "-b:v", bitrate,
                "-rc", "vbr", "-cq", "19", "-pix_fmt", "yuv420p"]
        # Parse Ada NVENC quality flags from config
        extra = config.get("video", {}).get("encoder_extra", "")
        if extra:
            import shlex
            args.extend(shlex.split(extra))
        return args
    return _get_video_codec()


def create_segment_mp4(seg_num: int, audio: Path, script: str,
                       out_dir: Path, config: dict, images: list | None = None,
                       word_timestamps_json: Path | None = None,
                       is_final: bool = True) -> Path:
    import shutil
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"segment_{seg_num:02d}.mp4"
    srt = out_dir / f"segment_{seg_num:02d}.srt"

    duration    = get_audio_duration(audio)

    # Write to a flat safe temporary path to bypass FFmpeg's single-quote escaping issues on Windows
    # Use UUID to prevent cross-contamination between concurrent pipeline runs
    temp_srt_dir = Path(f"temp_srt_files/{uuid.uuid4()}")
    temp_srt_dir.mkdir(parents=True, exist_ok=True)
    temp_srt = temp_srt_dir / f"segment_{seg_num:02d}.srt"

    try:
        sub_cfg = config.get("subtitles", {})
        format_style = sub_cfg.get("format", "classic")
        # P1-2: choose a Devanagari-capable default font when lang=hi and the user
        # has not explicitly set subtitles.font in config.
        # "Nirmala UI" ships with Windows 10/11 and has full Devanagari coverage.
        # "Noto Sans Devanagari" is the cross-platform fallback.
        _lang = config.get("tts", {}).get("lang", "en")
        if "font" in sub_cfg:
            # User explicitly set a font — always honour it.
            font = sub_cfg["font"]
        elif format_style == "tiktok":
            font = "Impact"
        elif _lang == "hi":
            font = "Nirmala UI"
        else:
            font = "Arial"
        size = sub_cfg.get("size", 38 if format_style == "tiktok" else 24)
        color = sub_cfg.get("color", "&H00FFFF&" if format_style == "tiktok" else "&HFFFFFF&")
        color_val = color.strip("&")

        if format_style == "tiktok":
            ass_style = f"Fontname={font},FontSize={size},PrimaryColour=\\&{color_val}\\&,OutlineColour=\\&H000000\\&,Outline=3,Shadow=0,Alignment=10"
        elif format_style == "classic":
            ass_style = f"Fontname={font},FontSize={size},PrimaryColour=\\&{color_val}\\&,OutlineColour=\\&H000000\\&,Outline=2,Shadow=1,Alignment=2,MarginV=30"
        else:
            ass_style = f"Fontname={font},FontSize={size},PrimaryColour=\\&{color_val}\\&,OutlineColour=\\&H000000\\&,Outline=2,Shadow=1,Alignment=2"

        _write_srt(script, temp_srt, duration, audio=audio, format_style=format_style,
                   word_timestamps_json=word_timestamps_json, is_final=is_final)
        res         = config["video"].get("resolution", "1920x1080")
        fps         = config["video"].get("fps", 24)
        # Bug 6: Escape paths for FFmpeg filtergraph on Windows correctly
        # Escape backslashes, colons (FFmpeg filter separator), and single quotes for filtergraph
        srt_path_str = str(temp_srt).replace("\\", "/").replace(":", "\\\\:").replace("'", "\\\\'")
        log.info(f"Seg {seg_num}: {duration:.1f}s | images={len(images) if images else 0}")

        if images:
            w, h    = res.split("x")
            total_frames = round(duration * fps)
            n_images = len(images)
            frames_per_image = total_frames // n_images
            rem = total_frames % n_images

            cmd = ["ffmpeg", "-y"]

            # Add all image inputs with explicit framerate to prevent 25fps default desync
            for idx, img in enumerate(images):
                img_frames = frames_per_image + (1 if idx < rem else 0)
                img_dur = img_frames / fps
                cmd.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{img_dur:.6f}", "-i", str(img)])

            # Add audio input
            audio_idx = len(images)
            cmd.extend(["-i", str(audio)])

            # Build filter_complex
            filter_parts = []
            concat_inputs = ""

            # Ken Burns mode: read from config (default "light")
            # "light" — fast scale+crop, near-instant on CPU (no per-frame zoompan)
            # "full"  — original zoompan (CPU-heavy, ~2s/frame; use only when time allows)
            # "off"   — plain scale to target size, no motion
            kb_mode = config.get("video", {}).get("ken_burns", "light")

            for idx in range(len(images)):
                img_frames_for_kb = frames_per_image + (1 if idx < rem else 0)
                img_dur_for_kb = img_frames_for_kb / fps

                if kb_mode == "full":
                    # Original zoompan — CPU-heavy (~2s/frame), kept for quality runs
                    kb_fps = min(12, fps)
                    kb_frames = max(1, int(img_dur_for_kb * kb_fps))
                    vf = (
                        f"[{idx}:v]scale={int(int(w)*1.25)}:{int(int(h)*1.25)},"
                        f"zoompan=z='min(zoom+0.0005,1.2)'"
                        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                        f":d={kb_frames}:s={w}x{h}:fps={kb_fps},"
                        f"fps={fps},setsar=1[v{idx}]"
                    )
                elif kb_mode == "off":
                    # Static: plain scale to target size, no motion
                    vf = (
                        f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                        f"setsar=1[v{idx}]"
                    )
                else:
                    # "light" (default): upscale 10% then center-crop — fast, no per-frame work
                    # Produces a gentle "zoomed-in" look without any CPU zoompan computation.
                    vf = (
                        f"[{idx}:v]scale={int(int(w)*1.1)}:-1,"
                        f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2,"
                        f"setsar=1[v{idx}]"
                    )
                filter_parts.append(vf)
                concat_inputs += f"[v{idx}]"

            # Concat the visual streams with crossfade transitions between images
            # xfade produces smooth 0.3s dissolves between consecutive clips
            crossfade_dur = config.get("video", {}).get("crossfade_duration", 0.3)

            if len(images) >= 2 and crossfade_dur > 0:
                # Chain xfade filters: [v0][v1]xfade→[xf0]; [xf0][v2]xfade→[xf1]; ...
                # Each xfade needs the offset (time where transition starts)
                _prev_label = "[v0]"
                for xf_idx in range(1, len(images)):
                    # Offset = cumulative duration of all previous clips minus crossfade overlaps
                    _prev_frames = sum(
                        frames_per_image + (1 if j < rem else 0)
                        for j in range(xf_idx)
                    )
                    _prev_dur = _prev_frames / fps
                    # Subtract accumulated crossfade overlaps from previous transitions
                    _offset = _prev_dur - (crossfade_dur * xf_idx)
                    _offset = max(0.1, _offset)  # safety floor
                    _out_label = f"[xf{xf_idx}]" if xf_idx < len(images) - 1 else "[v_concat]"
                    filter_parts.append(
                        f"{_prev_label}[v{xf_idx}]xfade=transition=fade"
                        f":duration={crossfade_dur}:offset={_offset:.3f}{_out_label}"
                    )
                    _prev_label = _out_label
            else:
                # Single image or crossfade disabled — simple concat
                filter_parts.append(f"{concat_inputs}concat=n={len(images)}:v=1:a=0[v_concat]")

            # P3-5 fix: each xfade consumes crossfade_dur of overlap, so the raw
            # concatenated video is shorter than the audio by (N-1)*crossfade_dur.
            # Compute the real post-xfade video duration and use it for fade_out_start.
            # The last image clip was already given the correct per-image duration above;
            # the xfade overlap is "consumed" from the tail of each preceding clip.
            # To make video == audio, we extend the last clip's input duration by the
            # total accumulated overlap before the filtergraph runs.
            # We do this by adjusting the last image's -t value in the input list.
            # Since we already built the cmd list, we patch the last image's duration arg.
            n_xfades = max(0, len(images) - 1) if (len(images) >= 2 and crossfade_dur > 0) else 0
            _total_overlap = n_xfades * crossfade_dur
            if _total_overlap > 0:
                # Find the -t argument for the last image input and extend it.
                # The last image input is at index (len(images)-1)*3+2 in the cmd list
                # (each image: ["-loop","1","-framerate",fps,"-t",dur,"-i",path] = 8 args).
                # Simpler: search backwards for the last "-t" before the audio "-i".
                _last_t_idx = None
                _audio_i_idx = None
                for _ci in range(len(cmd) - 1, -1, -1):
                    if cmd[_ci] == "-i" and _audio_i_idx is None:
                        _audio_i_idx = _ci
                    elif cmd[_ci] == "-t" and _audio_i_idx is not None and _last_t_idx is None:
                        _last_t_idx = _ci
                        break
                if _last_t_idx is not None:
                    try:
                        _old_dur = float(cmd[_last_t_idx + 1])
                        _new_dur = _old_dur + _total_overlap
                        cmd[_last_t_idx + 1] = f"{_new_dur:.6f}"
                        log.debug(
                            f"P3-5: extended last image clip {_old_dur:.3f}s → {_new_dur:.3f}s "
                            f"(+{_total_overlap:.3f}s overlap from {n_xfades} xfades)"
                        )
                    except (ValueError, IndexError):
                        pass

            # Real post-xfade video duration = audio duration (after the extension above)
            _real_video_dur = duration  # video now matches audio
            # Feature: Cinematic Fade-in / Fade-out per segment
            fade_out_start = max(0.0, _real_video_dur - 0.5)
            filter_parts.append(f"[v_concat]fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start:.2f}:d=0.5[v_faded]")

            filter_parts.append(f"[v_faded]subtitles='{srt_path_str}':force_style='{ass_style}'[v_final]")

            filter_complex = ";".join(filter_parts)

            filter_threads = config.get("performance", {}).get("ffmpeg_threads", 0)
            cmd.extend([
                "-filter_threads", str(filter_threads),
                "-filter_complex", filter_complex,
                "-map", "[v_final]",
                "-map", f"{audio_idx}:a"
            ])

            cmd.extend(_encoder_args(config))
            # D2: audio fade-out at segment end to smooth joins (fade-in handled by next segment)
            _xfade_ms = config.get("video", {}).get("audio_crossfade_ms", 0)
            _xfade_s = _xfade_ms / 1000.0
            if _xfade_s > 0:
                _fade_start = max(0.0, duration - _xfade_s)
                cmd.extend([
                    "-c:a", "aac", "-b:a", "128k",
                    "-af", f"afade=t=out:st={_fade_start:.3f}:d={_xfade_s:.3f},"
                           f"afade=t=in:st=0:d={_xfade_s:.3f}",
                    "-movflags", "+faststart",
                    str(mp4)
                ])
            else:
                cmd.extend([
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(mp4)
                ])

            log.info("Executing single-pass complex filtergraph for Ken Burns assembly...")
            # Scale timeout to video length and Ken Burns mode:
            # "full" (zoompan) is CPU-heavy (~2s/frame); keep the large formula.
            # "light"/"off" use scale+crop which is near-instant — much smaller timeout.
            if kb_mode == "full":
                _assembly_timeout = max(900, int(duration * 12) + 300)
            else:
                _assembly_timeout = max(300, int(duration * 4) + 120)
            _run(cmd, timeout=_assembly_timeout)

        else:
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={res}:d={duration}", "-r", str(fps), "-i", str(audio), *_encoder_args(config), "-c:a", "aac", "-b:a", "128k", "-vf", f"subtitles='{srt_path_str}':force_style='{ass_style}'", str(mp4)], timeout=300)

        # Copy temporary SRT back to its destination directory and clean up
        if temp_srt.exists():
            shutil.copy2(temp_srt, srt)

        # ENDURANCE MODE: Track intermediate assets for cleanup only after final concat succeeds.
        # Deletion is deferred to the pipeline orchestrator to preserve debugging artifacts.
        # Assets are tracked in cleanup_manifest.json per segment.
        # P4-4 fix: write the cleanup manifest only on the SUCCESS path (after _run completes
        # without raising), not in finally.  Writing on failure would record assets that may
        # still be needed for debugging or a retry.
        with _manifest_lock:
            try:
                import json as _json
                manifest_path = out_dir.parent / "cleanup_manifest.json"
                manifest = {}
                if manifest_path.exists():
                    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.setdefault("pending_cleanup", []).extend([
                    {"type": "audio", "path": str(audio)} if audio and audio.exists() else None,
                    *([{"type": "image", "path": str(Path(img))} for img in (images or []) if Path(img).exists()]),
                    {"type": "srt", "path": str(srt)} if srt.exists() else None,
                ])
                manifest["pending_cleanup"] = [e for e in manifest["pending_cleanup"] if e is not None]
                manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
                log.debug(f"Assets tracked for deferred cleanup: {len(manifest['pending_cleanup'])} files")
            except Exception as cleanup_err:
                log.debug(f"Cleanup manifest write failed: {cleanup_err}")

    finally:
        try:
            if temp_srt.exists():
                temp_srt.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            import shutil as _shutil
            _shutil.rmtree(temp_srt_dir, ignore_errors=True)
        except Exception:
            pass

    log.info(f"Segment saved: {mp4}")
    return mp4


def concatenate_segments(segments: list[Path], output: Path,
                          music: Path | None = None,
                          config: dict | None = None) -> Path:
    """Concatenate rendered segment MP4s into the final video.

    A3: When audio_fx.program_loudnorm is true, runs a 2-pass EBU R128 loudnorm
    on the final concatenated audio so segment seams don't pump in volume.
    """
    if not segments:
        raise ValueError("No segments to concatenate")
    output.parent.mkdir(parents=True, exist_ok=True)

    _cfg = config or {}
    _audio_fx = _cfg.get("audio_fx", {})
    _do_loudnorm = _audio_fx.get("program_loudnorm", False)
    _target_lufs = float(_audio_fx.get("target_lufs", -14))

    concat = output.parent / f"concat_list_{uuid.uuid4().hex[:8]}.txt"
    concat.write_text("\n".join(f"file '{p.absolute().as_posix()}'" for p in segments), encoding="utf-8")
    log.info(f"Concatenating {len(segments)} segments -> {output}")

    # ── Intermediate output (before loudnorm) ─────────────────────────────
    # If loudnorm is enabled we write to a temp file first, then apply loudnorm.
    _concat_out = output
    _temp_concat = None
    if _do_loudnorm:
        _temp_concat = output.parent / f"_concat_prenorm_{uuid.uuid4().hex[:8]}.mp4"
        _concat_out = _temp_concat

    if music and music.exists():
        log.info("Mixing background music (single-pass)...")
        # D5: music auto-ducking via sidechaincompress when music.ducking is true
        _music_cfg = _cfg.get("music", {})
        _do_ducking = _music_cfg.get("ducking", False)
        _duck_ratio = float(_music_cfg.get("duck_ratio", 0.3))
        # Map duck_ratio (0-1) to compressor ratio (1:1 = no compression, higher = more ducking)
        # duck_ratio 0.3 → compressor ratio ~4:1 (moderate ducking)
        _comp_ratio = max(1.5, 1.0 + _duck_ratio * 10)
        try:
            if _do_ducking:
                log.info(f"[D5] Music ducking enabled (ratio={_comp_ratio:.1f}:1)")
                _run([
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-stream_loop", "-1", "-i", str(music),
                    "-filter_threads", "0",
                    "-filter_complex",
                    # D5: asplit voice into mix copy + sidechain key;
                    # sidechaincompress ducks music under narration;
                    # amix blends ducked music with voice.
                    "[0:a]asplit=2[voice_mix][voice_key];"
                    "[1:a]volume=0.15,afade=t=in:st=0:d=3[music_in];"
                    f"[music_in][voice_key]sidechaincompress="
                    f"threshold=0.05:ratio={_comp_ratio:.1f}:attack=20:release=300[ducked];"
                    "[voice_mix][ducked]amix=inputs=2:duration=first:normalize=0[outa]",
                    "-map", "0:v", "-map", "[outa]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(_concat_out),
                ], timeout=900)
            else:
                _run([
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-stream_loop", "-1", "-i", str(music),
                    "-filter_threads", "0",
                    "-filter_complex",
                    "[1:a]volume=0.15,afade=t=in:st=0:d=3[bg];[0:a][bg]amix=inputs=2:duration=first[outa]",
                    "-map", "0:v", "-map", "[outa]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(_concat_out),
                ], timeout=900)
        finally:
            with contextlib.suppress(Exception):
                concat.unlink(missing_ok=True)
    else:
        log.info("No music provided, concatenating directly with homogenized audio...")
        try:
            _run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat), "-c:v", "copy",
                "-c:a", "aac", "-ar", "48000", "-b:a", "192k", str(_concat_out),
            ], timeout=600)
        finally:
            with contextlib.suppress(Exception):
                concat.unlink(missing_ok=True)

    # ── A3: 2-pass EBU R128 loudnorm ──────────────────────────────────────
    if _do_loudnorm and _temp_concat and _temp_concat.exists():
        log.info(f"[A3] Running 2-pass loudnorm (target {_target_lufs} LUFS)...")
        try:
            # Pass 1: measure
            _measure_filter = (
                f"loudnorm=I={_target_lufs}:TP=-1.5:LRA=11:print_format=json"
            )
            _p1 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(_temp_concat),
                 "-af", _measure_filter, "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", timeout=600
            )
            # Parse measured values from stderr JSON block
            import re as _re
            _json_match = _re.search(r'\{[^{}]*"input_i"[^{}]*\}', _p1.stderr, _re.DOTALL)
            if _json_match:
                import json as _json
                _measured = _json.loads(_json_match.group(0))
                _mi   = _measured.get("input_i", "-70")
                _mtp  = _measured.get("input_tp", "-2")
                _mlra = _measured.get("input_lra", "7")
                _mth  = _measured.get("input_thresh", "-80")
                _off  = _measured.get("target_offset", "0")
                # Pass 2: apply with linear=true
                _apply_filter = (
                    f"loudnorm=I={_target_lufs}:TP=-1.5:LRA=11"
                    f":measured_I={_mi}:measured_TP={_mtp}"
                    f":measured_LRA={_mlra}:measured_thresh={_mth}"
                    f":offset={_off}:linear=true"
                )
                _run([
                    "ffmpeg", "-y", "-i", str(_temp_concat),
                    "-af", _apply_filter,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    str(output),
                ], timeout=600)
                log.info(f"[A3] Loudnorm applied: {_mi} LUFS → {_target_lufs} LUFS")
            else:
                # Couldn't parse — fall back to single-pass
                log.warning("[A3] Could not parse loudnorm measurement; using single-pass fallback")
                _run([
                    "ffmpeg", "-y", "-i", str(_temp_concat),
                    "-af", f"loudnorm=I={_target_lufs}:TP=-1.5:LRA=11:linear=true",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    str(output),
                ], timeout=600)
        except Exception as _ln_err:
            log.warning(f"[A3] Loudnorm failed ({_ln_err}), using pre-norm output")
            import shutil as _shutil
            _shutil.copy2(str(_temp_concat), str(output))
        finally:
            with contextlib.suppress(Exception):
                _temp_concat.unlink(missing_ok=True)

    log.info(f"Final video: {output}")
    return output


# -- INTERNAL ---------------------------------------------------------------

def _write_srt(script: str, path: Path, duration: float, audio: Path | None = None,
               format_style: str = "classic", word_timestamps_json: Path | None = None,
               is_final: bool = True) -> None:
    """Write an SRT subtitle file for a segment.

    Timing priority (applies to ALL formats — tiktok and classic):
      1. word_timestamps_json — pre-computed word timestamps from the pipeline.
      2. Whisper transcription — if audio is provided and JSON is absent/empty.
      3. Proportional split — last resort when no timing data is available.

    Display style:
      - tiktok: one word per subtitle block (word-by-word karaoke style), uppercased.
      - classic: groups of CLASSIC_WORDS_PER_BLOCK words per block, sentence-cased.
    """
    # Number of words to group per subtitle block for classic format.
    CLASSIC_WORDS_PER_BLOCK = 4

    # ------------------------------------------------------------------ #
    # Step 1: Try word_timestamps_json (real audio timing, all formats)   #
    # ------------------------------------------------------------------ #
    if word_timestamps_json and word_timestamps_json.exists():
        try:
            log.info(f"Using provided word timestamps JSON for subtitles ({format_style}): {word_timestamps_json.name}")
            import json as _json
            word_data = _json.loads(word_timestamps_json.read_text(encoding="utf-8"))
            if word_data:
                lines = _words_to_srt_lines(word_data, format_style, CLASSIC_WORDS_PER_BLOCK)
                if lines:
                    path.write_text("\n".join(lines), encoding="utf-8-sig")
                    return
            else:
                log.warning("Word timestamps JSON is empty — falling back to Whisper/proportional")
        except Exception as e:
            log.warning(f"Failed to process word timestamps JSON: {e}. Falling back...")

    # ------------------------------------------------------------------ #
    # Step 2: Try Whisper transcription (real audio timing, all formats)  #
    # ------------------------------------------------------------------ #
    if audio is not None and audio.exists():
        try:
            log.warning(
                f"REGRESSION: Whisper fallback fired for seg (format={format_style}). "
                "TTS worker should have provided word_timestamps JSON. "
                "Check tts.alignment.enabled in config.yaml."
            )
            log.info(f"Generating word-level subtitles using Whisper ({format_style})...")
            with _whisper_lock:
                model = _get_whisper_model(is_final=is_final)
                if model is None:
                    raise RuntimeError("No whisper model available")

                if _whisper_backend == "faster":
                    segments_gen, _info = model.transcribe(
                        str(audio), beam_size=1, word_timestamps=True, vad_filter=True
                    )
                    raw_words = [
                        {"word": (w.word or "").strip(), "start": w.start, "end": w.end}
                        for seg in segments_gen
                        for w in (seg.words or [])
                        if (w.word or "").strip()
                    ]
                else:
                    result = model.transcribe(str(audio), word_timestamps=True)
                    raw_words = [
                        {"word": w.get("word", "").strip(), "start": w.get("start", 0.0), "end": w.get("end", 0.0)}
                        for seg in result.get("segments", [])
                        for w in seg.get("words", [])
                        if w.get("word", "").strip()
                    ]

            if raw_words:
                lines = _words_to_srt_lines(raw_words, format_style, CLASSIC_WORDS_PER_BLOCK)
                if lines:
                    path.write_text("\n".join(lines), encoding="utf-8-sig")
                    return
        except Exception as e:
            log.warning(f"Whisper word-level subtitle generation failed: {e}. Falling back to proportional split.")

    # ------------------------------------------------------------------ #
    # Step 3: Proportional split — last resort (no real timing available) #
    # Feature 6: Word-proportional SRT timing.                            #
    # Split into sentences, group into up to 8 subtitle blocks, allocate  #
    # time proportional to each block's word count.                       #
    # ------------------------------------------------------------------ #
    log.info(f"Using proportional split for subtitles ({format_style}) — no real timestamps available")
    sentences = [s.strip() for s in re.split(r"(?<!\d)\.(?=\s|$)|[!?।]+", script) if s.strip()]
    if not sentences:
        sentences = [script.strip()] if script.strip() else []
        if not sentences:
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\n \n\n", encoding="utf-8-sig")
            return

    MAX_BLOCKS = 8
    n_chunks = min(MAX_BLOCKS, len(sentences))
    chunk_size = max(1, math.ceil(len(sentences) / n_chunks))
    raw_chunks: list[str] = []
    i = 0
    while i < len(sentences):
        group = sentences[i: i + chunk_size]
        raw_chunks.append(". ".join(group))
        i += chunk_size
    chunks = raw_chunks[:MAX_BLOCKS]

    # Convert English periods in chunks back to mixed punctuation for Hindi
    chunks = [ch.replace(". ", " ") + "." if not any(d in ch for d in "।?!") else ch for ch in chunks]

    word_counts = [len(c.split()) for c in chunks]
    total_words = sum(word_counts) or 1

    lines: list[str] = []
    t = 0.0
    for idx, (text, wc) in enumerate(zip(chunks, word_counts, strict=False)):
        block_dur = max(0.5, (wc / total_words) * duration)
        t_end = t + block_dur
        lines += [
            str(idx + 1),
            f"{_ts(t)} --> {_ts(t_end)}",
            text,
            "",
        ]
        t = t_end

    path.write_text("\n".join(lines), encoding="utf-8-sig")


def _words_to_srt_lines(word_data: list, format_style: str, words_per_block: int) -> list[str]:
    """Convert a list of word-timestamp dicts to SRT lines.

    Args:
        word_data: list of {"word": str, "start": float, "end": float}
        format_style: "tiktok" → one word per block; "classic" → words_per_block words per block
        words_per_block: number of words to group per block for classic format

    Returns:
        List of SRT text lines (ready to join with "\\n").
    """
    # Filter out empty words
    words = [w for w in word_data if w.get("word", "").strip()]
    if not words:
        return []

    lines: list[str] = []
    idx = 1

    if format_style == "tiktok":
        # One word per subtitle block — karaoke style, uppercased
        for w in words:
            word = w["word"].strip().upper()
            start_str = _ts(w.get("start", 0.0))
            end_str = _ts(w.get("end", 0.0))
            lines += [str(idx), f"{start_str} --> {end_str}", word, ""]
            idx += 1
    else:
        # Group into blocks of words_per_block words — sentence-sized chunks
        for block_start in range(0, len(words), words_per_block):
            block = words[block_start: block_start + words_per_block]
            text = " ".join(w["word"].strip() for w in block)
            start_str = _ts(block[0].get("start", 0.0))
            end_str = _ts(block[-1].get("end", 0.0))
            lines += [str(idx), f"{start_str} --> {end_str}", text, ""]
            idx += 1

    return lines

def _ts(s: float) -> str:
    # Validate input
    if not isinstance(s, (int, float)):
        log.warning(f"Invalid timestamp value: {s}")
        return "00:00:00,000"
    if s < 0 or math.isnan(s) or s == float('inf'):  # NaN or Inf check
        log.warning(f"Invalid timestamp: {s}")
        return "00:00:00,000"

    try:
        h, rem = divmod(int(s), 3600)
        m, sec = divmod(rem, 60)
        ms = round((s % 1) * 1000)
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
    except (ValueError, OverflowError) as e:
        log.warning(f"Timestamp conversion failed: {e}")
        return "00:00:00,000"


def _run(cmd: list, timeout: int = 300) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode(errors='replace')
            # Only suppress purely deprecation warnings (no other error indicators)
            critical_indicators = ['error', 'failed', 'unable', 'invalid', 'no such', 'cannot']
            is_critical = any(ind in stderr.lower() for ind in critical_indicators)
            if is_critical or 'deprecated' not in stderr.lower():
                raise RuntimeError(f"ffmpeg error: {stderr[-1000:]}")
            else:
                log.warning(f"FFmpeg deprecation warning (non-fatal): {stderr[:200]}")

    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg timeout (>{timeout}s)") from e
    except Exception as e:
        raise RuntimeError(f"ffmpeg failed: {e}") from e
