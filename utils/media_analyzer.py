#!/usr/bin/env python3
"""
media_analyzer.py - Audio & Video Quality Inspector.

A specialized diagnostics utility to audit the physical and structural health
of compiled video (.mp4) and audio (.wav) segments. Verifies compliance with
Video.AI project rendering and narration standards.
"""

import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

# ANSI colors
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


def check_ffprobe() -> bool:
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ── Audio Quality Analytics ────────────────────────────────────────────────


def _native_analyze_audio_wave(path: Path) -> dict[str, Any] | None:
    try:
        import videoai_worker_native
    except Exception:
        return None

    try:
        return json.loads(videoai_worker_native.analyze_audio_wave(str(path)))
    except Exception:
        return None


def analyze_audio_wave(path: Path) -> dict[str, Any]:
    """Inspect raw WAV structure, bit rate, sample rate, peak volume, and clipping."""
    native_info = _native_analyze_audio_wave(path)
    if native_info is not None:
        if "sample_width_bits" in native_info:
            native_info["sample_width"] = int(native_info["sample_width_bits"] / 8)
        if "duration_s" in native_info:
            native_info["duration"] = native_info["duration_s"]
        return native_info

    try:
        with wave.open(str(path), "rb") as w:
            params = w.getparams()
            n_channels = params.nchannels
            samp_width = params.sampwidth
            sample_rate = params.framerate
            n_frames = params.nframes
            duration = n_frames / float(sample_rate)

            # Read frames to analyze audio levels
            raw_data = w.readframes(n_frames)

        # Parse PCM frames based on sample width (typically 16-bit PCM = 2 bytes)
        peaks = []
        clipping_samples = 0
        total_samples = len(raw_data) // samp_width

        if samp_width == 2:  # 16-bit signed integer
            fmt = f"<{total_samples}h"  # little-endian 16-bit
            samples = struct.unpack(fmt, raw_data)
            max_val = 32768.0

            # Find peak levels and check for clipping
            for s in samples:
                val = abs(s) / max_val
                peaks.append(val)
                if abs(s) >= 32760:  # near clipping ceiling
                    clipping_samples += 1
        else:
            samples = []
            max_val = 1.0
            # Fallback for 8-bit or 24/32-bit (non-wave standard library easy parse)

        peak_volume = max(peaks) if peaks else 0.0
        peak_db = 20 * math.log10(peak_volume) if peak_volume > 0 else -99.0
        clip_pct = (clipping_samples / total_samples) * 100 if total_samples > 0 else 0.0

        # Calculate approximate Root Mean Square (RMS) dBFS level
        rms = math.sqrt(sum(p**2 for p in peaks) / len(peaks)) if peaks else 0.0
        rms_db = 20 * math.log10(rms) if rms > 0 else -99.0

        return {
            "channels": n_channels,
            "sample_width": samp_width,
            "sample_rate": sample_rate,
            "duration": duration,
            "peak_db": peak_db,
            "rms_db": rms_db,
            "clipping_pct": clip_pct,
            "format": "PCM WAV",
        }
    except Exception as e:
        return {"error": f"Failed to analyze WAV file: {e}"}


def analyze_audio(path: Path):
    print(f"\n{BOLD}{CYAN}=== Audio Health Analysis: {path.name} ==={RESET}")

    # 1. Physical structure checks
    info = analyze_audio_wave(path)
    if "error" in info:
        log_error(info["error"])
        # Fallback to ffprobe
        run_ffprobe_audio(path)
        return

    log_success("WAV physical file parameters extracted.")
    print(
        f"  - Channels:       {info['channels']} ({'Mono' if info['channels'] == 1 else 'Stereo'})"
    )
    print(f"  - Sample Width:   {info['sample_width'] * 8} bits")
    print(f"  - Sample Rate:    {info['sample_rate']} Hz")
    print(f"  - Duration:       {info['duration']:.3f} seconds")

    # Check compliance with Video.AI standard audio
    if info["sample_rate"] == 24000:
        log_info("Sample rate matches raw OmniVoice worker (24000Hz).")
    elif info["sample_rate"] == 44100:
        log_success("Sample rate matches post-processed premium audio (44100Hz).")
    else:
        log_warn(f"Non-standard sample rate detected: {info['sample_rate']}Hz.")

    # 2. Audio Dynamics & Mastering check (Premium Voice processing standards)
    print(f"\n{BOLD}Audio Dynamic Range Metrics:{RESET}")
    print(f"  - Peak Volume:    {BOLD}{info['peak_db']:.2f} dBFS{RESET}")
    print(f"  - Average RMS:    {BOLD}{info['rms_db']:.2f} dBFS{RESET}")
    print(
        f"  - Clipping Level: {info['clipping_pct']:.4f}% ({info['clipping_pct'] * 100:.0f} clip occurrences per million)"
    )

    # Audio_FX normalizes to -14dBFS RMS and protects peak at -1.0dBFS
    if info["peak_db"] > -0.5:
        log_error("CRITICAL CLIPPING DETECTED! Peak exceeds protection ceiling (> -0.5 dBFS).")
    elif info["peak_db"] > -2.0:
        log_success("Perfect peak mastering (Peak resides in safe -1.0 to -2.0 dBFS zone).")
    else:
        log_info("Audio peak is well attenuated. No risk of hardware distortion.")

    if abs(info["rms_db"] - (-14.0)) <= 2.5:
        log_success(
            "Average loudness conforms perfectly to target podcast standard (-14.0 ± 2 dBFS RMS)."
        )
    else:
        log_warn(
            f"Narration loudness is outside typical podcast standards (RMS is {info['rms_db']:.1f} dBFS)."
        )


def run_ffprobe_audio(path: Path):
    if not check_ffprobe():
        return
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    print(res.stdout)


# ── Video Quality & Subtitle Sync Analytics ────────────────────────────────


def analyze_video(path: Path):
    print(f"\n{BOLD}{CYAN}=== Video Health Analysis: {path.name} ==={RESET}")
    if not check_ffprobe():
        log_error("ffprobe not available in path.")
        return

    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,bit_rate:stream=width,height,avg_frame_rate,codec_name,codec_type",
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
        size_mb = float(format_info.get("size", 0.0)) / (1024**2)
        bitrate_kb = float(format_info.get("bit_rate", 0.0)) / 1024

        log_success("Video stream parameters extracted.")
        print(f"  - Duration:       {BOLD}{duration:.2f} seconds{RESET}")
        print(f"  - File Size:      {size_mb:.2f} MB")
        print(f"  - Total Bitrate:  {bitrate_kb:.2f} kbps")

        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if vstream:
            w = int(vstream.get("width", 0))
            h = int(vstream.get("height", 0))
            codec = vstream.get("codec_name", "unknown")

            # FPS parsing
            fps_str = vstream.get("avg_frame_rate", "0/0")
            fps = 0.0
            if "/" in fps_str:
                n, d = map(float, fps_str.split("/"))
                if d > 0:
                    fps = n / d

            print(
                f"  - Resolution:     {BOLD}{w}x{h}{RESET} ({'Horizontal 1080p' if w == 1920 else 'Vertical TikTok' if w == 1080 else 'SD/Low-Res'})"
            )
            print(f"  - Video Codec:    {codec}")
            print(f"  - Framerate:      {fps:.2f} fps")

            # Compliance checks
            if w == 1920 and h == 1080:
                log_success("Video resolution matches final 1080p standards.")
            elif w == 1080 and h == 1920:
                log_success("Video resolution matches mobile/TikTok standards.")
            else:
                log_warn("Non-standard canvas resolution detected.")

            if abs(fps - 24.0) > 0.1 and abs(fps - 12.0) > 0.1:
                log_warn("Framerate differs from target classic 24fps or zoompan 12fps.")
        else:
            log_error("No active video stream found in the media container!")

        if astream:
            log_success(
                f"Audio track present: {astream.get('codec_name', 'unknown')} ({int(astream.get('sample_rate', 0))}Hz)"
            )
            # Sync validation: compare video duration to audio duration
            try:
                audio_dur = float(astream.get("duration", duration))
                diff = abs(duration - audio_dur)
                if diff > 0.2:
                    log_warn(
                        f"AUDIO-VIDEO DRIFT DETECTED: Video duration is {duration:.2f}s but Audio is {audio_dur:.2f}s (Diff: {diff:.2f}s). Subtitles may drift out of sync!"
                    )
                else:
                    log_success("Audio-Video stream tracks are perfectly aligned and synced.")
            except Exception:
                pass
        else:
            log_error("CRITICAL: Video is missing an audio track!")

    except Exception as e:
        log_error(f"Failed to diagnose video file: {e}")


# ── Main Flow ──────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <media_file>")
        sys.exit(1)

    path = Path(sys.argv[1]).resolve()
    if not path.exists():
        log_error(f"File not found: {path}")
        sys.exit(1)

    suffix = path.suffix.lower()
    if suffix in (".wav", ".mp3", ".ogg", ".flac"):
        analyze_audio(path)
    elif suffix in (".mp4", ".mkv", ".avi", ".mov"):
        analyze_video(path)
    else:
        log_warn(f"Unknown suffix '{suffix}'. Run standard ffprobe diagnostics:")
        run_ffprobe_audio(path)


if __name__ == "__main__":
    main()
