"""audio_fx.py - Sound effects mixing for video segments."""

import json
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path

from utils import get_audio_duration
from utils.path_utils import is_safe_path

log = logging.getLogger(__name__)

# SFX keyword-to-file mapping.
# Status: only "thunder" has an active file in sfx/. All other entries are
# commented out because the corresponding WAV files do not yet exist.
# To add more SFX: drop a WAV file in sfx/ and uncomment (or add) an entry here.
_DEFAULT_SFX = {
    "thunder": "sfx/thunder.wav",
}

# Check if sfx directory exists
if not Path("sfx").exists():
    log.warning("sfx/ directory not found - SFX mixing will be skipped")


def _try_native_audio_master(input_path: Path, output_path: Path) -> bool:
    """Use the optional PyO3 Rust extension when explicitly enabled.

    The import is intentionally lazy so normal source installs keep using the
    established Python/pydub path. Native mastering is guarded by
    VIDEOAI_RUST_AUDIO=1 because the Rust implementation currently represents
    the fallback FFmpeg chain rather than full premium pydub parity.
    """
    if os.environ.get("VIDEOAI_RUST_AUDIO") != "1":
        return False

    try:
        import videoai_worker_native
    except Exception:
        return False

    try:
        report = json.loads(videoai_worker_native.master_audio(str(input_path), str(output_path)))
        if report.get("passed") and output_path.exists():
            log.info("Native Rust audio mastering done: %s", output_path.name)
            return True
        log.debug("Native Rust audio mastering declined: %s", report)
    except Exception as exc:
        log.debug("Native Rust audio mastering failed; falling back to Python: %s", exc)
    return False


def mix_sfx(
    audio_path: Path, script: str, output_dir: Path, segment_idx: int, sfx_volume: float = 0.25
) -> Path:
    """Mix relevant sound effects into the audio track based on script content.

    Scans the script for keywords and overlays matching SFX files
    at random intervals using FFmpeg.

    Args:
        audio_path: Path to the voiceover WAV file
        script: English script text (used for keyword detection)
        output_dir: Directory to save the mixed audio
        sfx_volume: Volume multiplier for SFX (0.0 - 1.0)

    Returns:
        Path to the mixed audio file (original if mixing fails)
    """

    def _record_sfx_degradation(reason: str) -> None:
        try:
            from agents.director_agent import UIState

            UIState.add_degradation(segment_idx, "sfx_skip", reason)
        except Exception:
            pass

    if not audio_path.exists():
        log.warning(f"Audio file not found: {audio_path} — skipping SFX")
        return audio_path

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"voiceover_with_sfx_{segment_idx:02d}.wav"

    if not is_safe_path(output_dir, str(output_path)):
        log.warning(f"Output path escapes output directory: {output_path}")
        return audio_path

    # Detect relevant SFX from script
    script_lower = script.lower()
    matched_sfx = []
    for keyword, sfx_path_str in _DEFAULT_SFX.items():
        if keyword in script_lower:
            sfx_path = Path(sfx_path_str)
            if sfx_path.exists():
                matched_sfx.append(sfx_path)
                log.info(f"Matched SFX: {keyword} -> {sfx_path}")

    if not matched_sfx:
        log.info("No matching SFX found — copying audio as-is")
        shutil.copy(audio_path, output_path)
        return output_path

    # Build FFmpeg filter for SFX mixing
    # Use amix to overlay SFX at random positions
    try:
        duration = get_audio_duration(audio_path)
        if duration <= 0:
            log.warning("Invalid audio duration — skipping SFX")
            shutil.copy(audio_path, output_path)
            return output_path

        sfx_to_mix = matched_sfx[:5]  # Limit to 5 SFX max
        filter_parts = []
        sfx_inputs = []

        for i, sfx in enumerate(sfx_to_mix):
            sfx_dur = get_audio_duration(sfx)
            if sfx_dur <= 0:
                sfx_dur = 2.0

            # Random position within first 80% of audio
            pos = random.uniform(0, max(0.1, duration * 0.8))
            sfx_inputs.extend(["-i", str(sfx)])
            clamped_vol = max(0.0, min(1.0, float(sfx_volume)))

            # Smooth fade-out relative to the individual SFX file's duration to prevent audio pops
            fade_start = max(0.0, sfx_dur - 1.0)

            # P3-7 fix: normalise each SFX input to 44100 Hz stereo before mixing.
            # Without this, a mono or 22050 Hz SFX file fed directly to amix can
            # cause ffmpeg to fail the filtergraph or silently pitch-shift the SFX
            # (rate mismatch) or produce a mono output (layout mismatch).
            filter_parts.append(
                f"[{i + 1}:a]aresample=44100,aformat=channel_layouts=stereo"
                f",adelay={int(pos * 1000)}|{int(pos * 1000)}"
                f",afade=t=out:st={fade_start:.2f}:d=1.0"
                f",volume={clamped_vol}[sfx{i}]"
            )

        filter_str = ";".join(filter_parts)
        # P2-15: Set voice at full volume (1.0) explicitly before mixing so that
        # amix:normalize=0 preserves the original voice level regardless of how
        # many SFX streams are added.  Without normalize=0, amix scales every
        # input by 1/(N+1), dropping the voice by ~6 dB with a single SFX track.
        # P3-7: also resample the voice to 44100 Hz stereo so all amix inputs
        # share the same rate/layout (avoids filtergraph errors on mismatched inputs).
        filter_str += ";[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.0[voice]"
        sfx_labels = "".join(f"[sfx{i}]" for i in range(len(sfx_to_mix)))
        filter_str += (
            f";[voice]{sfx_labels}"
            f"amix=inputs={len(sfx_to_mix) + 1}:duration=first:normalize=0[outa]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            *sfx_inputs,
            "-filter_complex",
            filter_str,
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]

        log.info(f"Mixing {len(matched_sfx)} SFX tracks")
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
        if result.returncode != 0:
            log.warning(f"SFX mixing failed: {result.stderr.decode(errors='replace')[:100]}")
            shutil.copy(audio_path, output_path)
            _record_sfx_degradation("SFX mix FFmpeg error")
            return output_path
        log.info(f"SFX mixed -> {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        log.warning("SFX mixing timeout — using raw audio")
        shutil.copy(audio_path, output_path)
        _record_sfx_degradation("SFX mix timeout")
        return output_path
    except Exception as e:
        log.warning(f"SFX mixing failed ({e}) — using raw audio")
        shutil.copy(audio_path, output_path)
        _record_sfx_degradation(f"SFX mix exception: {str(e)[:80]}")
        return output_path


def apply_premium_voice_processing(input_path: Path, output_path: Path) -> bool:
    """Apply gentle post-processing tuned for OmniVoice TTS output:
    1. Trim excessive silent gaps (>800ms -> 500ms)
    2. Upsample to 44100Hz
    3. Gentle dynamic compression (2:1, threshold -24dB) to even out micro-dynamics
    4. Light de-esser: reduce harsh sibilance above 6kHz
    5. RMS Normalization to -14.0 dBFS with peak protection at -1.0 dBFS
    """
    if _try_native_audio_master(input_path, output_path):
        return True

    try:
        from pydub import AudioSegment
        from pydub.effects import compress_dynamic_range
        from pydub.silence import detect_silence

        log.info(f"Applying gentle voice processing to {input_path.name}...")

        sound = AudioSegment.from_file(str(input_path))

        # 1. Only trim extremely long silences (>800ms -> 500ms)
        # Preserve natural speech pauses
        silences = detect_silence(sound, min_silence_len=800, silence_thresh=-45)
        if silences:
            chunks = []
            last_end = 0
            for start, end in silences:
                if start > last_end:
                    chunks.append(sound[last_end:start])
                chunks.append(sound[start : start + 500])
                last_end = end
            if last_end < len(sound):
                chunks.append(sound[last_end:])
            trimmed = AudioSegment.empty()
            for chunk in chunks:
                trimmed += chunk
            sound = trimmed

        # 2. Upsample to 44100Hz
        sound = sound.set_frame_rate(44100)

        # 3. Gentle compression — just tame peaks, don't crush dynamics
        sound = compress_dynamic_range(
            sound, threshold=-24.0, ratio=2.0, attack=10.0, release=100.0
        )

        # 4. Light de-esser: reduce sibilance harshness above 6kHz
        try:
            sibilant = sound.high_pass_filter(6000)
            non_sibilant = sound.low_pass_filter(6000)
            sound = non_sibilant.overlay(sibilant.apply_gain(-3.0))
        except Exception:
            pass  # non-fatal

        # 5. P3-6 fix: apply peak limiting BEFORE loudness normalization.
        # Previously: gain to -14 dBFS first, then check peak → peaks could clip
        # before protection ran.  Correct order: clamp peaks first, then normalize.
        # Step 5a: Peak limiting — clamp to -1.0 dBFS before any gain change
        if sound.max_dBFS > -1.0:
            peak_gain = -1.0 - sound.max_dBFS
            sound = sound.apply_gain(peak_gain)
        # Step 5b: Loudness normalization to -14 dBFS (streaming standard)
        target_dbfs = -14.0
        gain_needed = target_dbfs - sound.dBFS
        sound = sound.apply_gain(gain_needed)
        # Step 5c: Final safety check — if normalization pushed peaks above -1 dBFS,
        # clamp again (handles edge case where the signal is very peaky).
        if sound.max_dBFS > -1.0:
            peak_gain = -1.0 - sound.max_dBFS
            sound = sound.apply_gain(peak_gain)

        sound.export(str(output_path), format="wav")
        log.info(f"Gentle voice processing done: {output_path.name}")
        return True
    except Exception as e:
        log.error(f"Voice processing failed ({e})", exc_info=True)
        return False


def master_audio(audio_path: Path, output_dir: Path, segment_idx: int) -> Path:
    """Apply premium mastering chain to voiceover track for clarity and crisp highs:
    Attempts to apply our high-quality numpy/scipy studio-grade voice processing
    including a treble harmonic exciter and gap trimming.
    Falls back to ffmpeg light mastering if it fails or dependencies are missing.
    """

    def _record_master_degradation(reason: str) -> None:
        try:
            from agents.director_agent import UIState

            UIState.add_degradation(segment_idx, "mastering_fallback", reason)
        except Exception:
            pass

    if not audio_path.exists():
        log.warning(f"Audio file not found for mastering: {audio_path}")
        return audio_path

    # Skip voice processing for silent audio fallback files
    if "silence" in audio_path.name.lower():
        log.debug(f"Skipping voice processing for silent audio: {audio_path.name}")
        return audio_path

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"mastered_audio_{segment_idx:02d}.wav"

    if not is_safe_path(output_dir, str(output_path)):
        log.warning(f"Output path escapes output directory: {output_path}")
        return audio_path

    # Try applying the premium python/numpy/scipy voice processing
    success = apply_premium_voice_processing(audio_path, output_path)
    if success:
        return output_path

    log.info("Premium voice processing unavailable (scipy/numpy deps). Using ffmpeg mastering...")

    # Gentle fallback chain for OmniVoice output
    audio_filter = (
        "highpass=f=60,"
        "acompressor=threshold=-24dB:ratio=2:attack=10:release=100,"
        "loudnorm=I=-14:TP=-1.5:LRA=9"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-af",
        audio_filter,
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    try:
        log.info(
            f"Mastering audio ({audio_path.name}) — fallback light chain: HPF 60Hz, 2:1 comp, -14 LUFS LRA 9..."
        )
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            log.warning(
                f"Audio mastering fallback failed, falling back to original copy: {stderr[-500:]}"
            )
            shutil.copy(audio_path, output_path)
            _record_master_degradation("FFmpeg mastering error")
            return output_path

        log.info(f"Audio mastered successfully (fallback): {output_path}")
        return output_path
    except subprocess.TimeoutExpired:
        log.warning("Audio mastering fallback timed out, falling back to original copy")
        shutil.copy(audio_path, output_path)
        _record_master_degradation("mastering timeout")
        return output_path
    except Exception as e:
        log.warning(f"Audio mastering fallback failed ({e}), falling back to original copy")
        shutil.copy(audio_path, output_path)
        _record_master_degradation(f"mastering exception: {str(e)[:80]}")
        return output_path
