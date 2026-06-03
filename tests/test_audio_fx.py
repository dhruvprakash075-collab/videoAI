"""test_audio_fx.py - mix_sfx, apply_premium_voice_processing, master_audio."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from audio.audio_fx import (
    _DEFAULT_SFX,
    apply_premium_voice_processing,
    master_audio,
    mix_sfx,
)

# ── mix_sfx ───────────────────────────────────────────────────────────────────


def test_mix_sfx_missing_audio_returns_input(tmp_path: Path):
    out = mix_sfx(tmp_path / "missing.wav", "thunder", tmp_path, 1)
    assert out == tmp_path / "missing.wav"


def test_mix_sfx_no_matching_keywords_copies(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    out = mix_sfx(src, "completely unrelated text", tmp_path, 1)
    assert out.exists()
    assert out.read_bytes() == b"RIFF"


def test_mix_sfx_thunder_keyword_match(tmp_path: Path, monkeypatch):
    # Create a fake sfx directory with a thunder.wav
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")

    # Patch Path("sfx") to point to our temp dir
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)

    with (
        patch("subprocess.run") as run_mock,
        patch("audio.audio_fx.get_audio_duration", return_value=30.0),
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        mix_sfx(src, "the thunder rolls", tmp_path, 1)
    assert run_mock.called
    # ffmpeg should have been called
    cmd = run_mock.call_args.args[0]
    assert "ffmpeg" in cmd[0]


def test_mix_sfx_ffmpeg_error_returns_copy(tmp_path: Path, monkeypatch):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)
    with (
        patch("subprocess.run") as run_mock,
        patch("audio.audio_fx.get_audio_duration", return_value=30.0),
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"ffmpeg error"
        )
        out = mix_sfx(src, "the thunder rolls", tmp_path, 1)
    assert out.exists()
    assert out.read_bytes() == b"RIFF"


def test_mix_sfx_timeout_returns_copy(tmp_path: Path, monkeypatch):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)),
        patch("audio.audio_fx.get_audio_duration", return_value=30.0),
    ):
        out = mix_sfx(src, "the thunder rolls", tmp_path, 1)
    assert out.exists()


def test_mix_sfx_exception_returns_copy(tmp_path: Path, monkeypatch):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)
    with (
        patch("subprocess.run", side_effect=RuntimeError("boom")),
        patch("audio.audio_fx.get_audio_duration", return_value=30.0),
    ):
        out = mix_sfx(src, "the thunder rolls", tmp_path, 1)
    assert out.exists()


def test_mix_sfx_zero_duration_copies(tmp_path: Path, monkeypatch):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)
    with (
        patch("subprocess.run") as run_mock,
        patch("audio.audio_fx.get_audio_duration", return_value=0.0),
    ):
        out = mix_sfx(src, "the thunder rolls", tmp_path, 1)
    # ffmpeg should NOT be called when duration is 0
    assert not run_mock.called
    assert out.exists()


def test_mix_sfx_clips_volume_to_range(tmp_path: Path, monkeypatch):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "thunder.wav").write_bytes(b"x")
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    real_path = Path

    def fake_path(p):
        if str(p) == "sfx":
            return sfx_dir
        if str(p).startswith("sfx/"):
            return sfx_dir / str(p).split("/", 1)[1]
        return real_path(p)

    monkeypatch.setattr("audio.audio_fx.Path", fake_path)
    with (
        patch("subprocess.run") as run_mock,
        patch("audio.audio_fx.get_audio_duration", return_value=30.0),
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        # Volume > 1.0 should be clipped
        mix_sfx(src, "the thunder rolls", tmp_path, 1, sfx_volume=2.0)
    # Find the ffmpeg call args — subprocess.run uses positional cmd list
    cmd = run_mock.call_args.args[0]
    # Locate the -filter_complex flag and the following value
    fc_idx = cmd.index("-filter_complex")
    filter_str = cmd[fc_idx + 1]
    assert "volume=1.0" in filter_str


# ── apply_premium_voice_processing ───────────────────────────────────────────


def test_apply_premium_voice_processing_missing_input(tmp_path: Path):
    # Should handle missing file gracefully
    with patch("pydub.AudioSegment.from_file", side_effect=FileNotFoundError):
        out = apply_premium_voice_processing(tmp_path / "missing.wav", tmp_path / "out.wav")
    assert out is False


def _make_sound_mock(max_dbfs=-3.0, dbfs=-20.0, length_ms=5000):
    """Build a self-referential AudioSegment-like mock that survives chained calls."""
    sound = MagicMock(name="AudioSegment")
    sound.max_dBFS = max_dbfs
    sound.dBFS = dbfs
    # Chained methods return `sound` itself so the pipeline keeps working
    sound.set_frame_rate.return_value = sound
    sound.high_pass_filter.return_value = sound
    sound.low_pass_filter.return_value = sound
    sound.apply_gain.return_value = sound
    sound.overlay.return_value = sound
    sound.__len__.return_value = length_ms
    sound.__getitem__.return_value = sound
    # empty() returns a new sound (used in trim path)
    sound.empty.return_value = sound
    return sound


def test_apply_premium_voice_processing_full_pipeline(tmp_path: Path):
    """Mock the entire pydub chain to verify the pipeline runs."""
    sound = _make_sound_mock()
    with (
        patch("pydub.AudioSegment.from_file", return_value=sound),
        patch("pydub.effects.compress_dynamic_range", return_value=sound),
        patch("pydub.silence.detect_silence", return_value=[]),
        patch("pydub.AudioSegment.empty", return_value=sound),
    ):
        out = apply_premium_voice_processing(tmp_path / "in.wav", tmp_path / "out.wav")
    assert out is True
    sound.export.assert_called_once()


def test_apply_premium_voice_processing_with_silences(tmp_path: Path):
    """When silences are detected, they are trimmed to 500ms chunks."""
    sound = _make_sound_mock()
    silence_segments = [(1000, 2000)]  # 1 second silence in middle
    with (
        patch("pydub.AudioSegment.from_file", return_value=sound),
        patch("pydub.effects.compress_dynamic_range", return_value=sound),
        patch("pydub.silence.detect_silence", return_value=silence_segments),
        patch("pydub.AudioSegment.empty", return_value=sound),
    ):
        out = apply_premium_voice_processing(tmp_path / "in.wav", tmp_path / "out.wav")
    assert out is True
    sound.export.assert_called_once()


def test_apply_premium_voice_processing_de_esser_fails(tmp_path: Path):
    """If the de-esser filter fails, the pipeline still succeeds."""
    sound = _make_sound_mock(max_dbfs=-10.0, dbfs=-14.0)
    sound.high_pass_filter.side_effect = RuntimeError("hpf fail")
    with (
        patch("pydub.AudioSegment.from_file", return_value=sound),
        patch("pydub.effects.compress_dynamic_range", return_value=sound),
        patch("pydub.silence.detect_silence", return_value=[]),
        patch("pydub.AudioSegment.empty", return_value=sound),
    ):
        out = apply_premium_voice_processing(tmp_path / "in.wav", tmp_path / "out.wav")
    assert out is True


# ── master_audio ──────────────────────────────────────────────────────────────


def test_master_audio_missing_input(tmp_path: Path):
    out = master_audio(tmp_path / "missing.wav", tmp_path, 1)
    assert out == tmp_path / "missing.wav"


def test_master_audio_silence_skip(tmp_path: Path):
    """Silent audio files are returned unchanged (skip processing)."""
    src = tmp_path / "silence_gen.wav"
    src.write_bytes(b"x")
    out = master_audio(src, tmp_path, 1)
    assert out == src


def test_master_audio_uses_premium_when_available(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"x")
    with patch("audio.audio_fx.apply_premium_voice_processing", return_value=True) as pp:
        out = master_audio(src, tmp_path, 1)
    assert pp.called
    # Output is the premium-processed file
    assert out == tmp_path / "mastered_audio_01.wav"


def test_master_audio_falls_back_to_ffmpeg(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"x")
    with (
        patch("audio.audio_fx.apply_premium_voice_processing", return_value=False),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        out = master_audio(src, tmp_path, 1)
    assert run_mock.called
    assert out == tmp_path / "mastered_audio_01.wav"


def test_master_audio_ffmpeg_error_returns_copy(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"original")
    with (
        patch("audio.audio_fx.apply_premium_voice_processing", return_value=False),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"ffmpeg failed"
        )
        out = master_audio(src, tmp_path, 1)
    # Should fall back to copying the original
    assert out.read_bytes() == b"original"


def test_master_audio_ffmpeg_timeout_returns_copy(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"original")
    with (
        patch("audio.audio_fx.apply_premium_voice_processing", return_value=False),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)),
    ):
        out = master_audio(src, tmp_path, 1)
    assert out.read_bytes() == b"original"


def test_master_audio_ffmpeg_exception_returns_copy(tmp_path: Path):
    src = tmp_path / "voice.wav"
    src.write_bytes(b"original")
    with (
        patch("audio.audio_fx.apply_premium_voice_processing", return_value=False),
        patch("subprocess.run", side_effect=RuntimeError("boom")),
    ):
        out = master_audio(src, tmp_path, 1)
    assert out.read_bytes() == b"original"


def test_default_sfx_contains_thunder():
    assert "thunder" in _DEFAULT_SFX
    assert _DEFAULT_SFX["thunder"] == "sfx/thunder.wav"
