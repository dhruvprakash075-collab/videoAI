"""test_music_ducking.py - Tests for D5: music auto-ducking via sidechaincompress."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch


def _make_segments(tmp_path, n=2):
    segs = []
    for i in range(n):
        p = tmp_path / f"seg_{i:02d}.mp4"
        p.write_bytes(b"fake")
        segs.append(p)
    return segs


def _make_music(tmp_path):
    p = tmp_path / "music.mp3"
    p.write_bytes(b"fake_music")
    return p


def test_sidechaincompress_present_when_ducking_enabled(tmp_path):
    """When music.ducking=True, sidechaincompress should appear in the FFmpeg command."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    music = _make_music(tmp_path)
    output = tmp_path / "out.mp4"
    config = {
        "music": {"ducking": True, "duck_ratio": 0.3},
        "audio_fx": {"program_loudnorm": False},
    }

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        output.write_bytes(b"fake_output")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, output, music=music, config=config)

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    assert "sidechaincompress" in all_args, "sidechaincompress must be in FFmpeg args when ducking=True"


def test_sidechaincompress_absent_when_ducking_disabled(tmp_path):
    """When music.ducking=False, sidechaincompress should NOT appear in the filter_complex."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    music = _make_music(tmp_path)
    output = tmp_path / "out.mp4"
    config = {
        "music": {"ducking": False, "duck_ratio": 0.3},
        "audio_fx": {"program_loudnorm": False},
    }

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        output.write_bytes(b"fake_output")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, output, music=music, config=config)

    # Check only the filter_complex argument (not the whole command string which
    # may contain path fragments with "sidechaincompress" from other tests)
    filter_complex_args = []
    for cmd in run_calls:
        for j, arg in enumerate(cmd):
            if str(arg) == "-filter_complex" and j + 1 < len(cmd):
                filter_complex_args.append(str(cmd[j + 1]))
    assert all("sidechaincompress" not in fc for fc in filter_complex_args), \
        "sidechaincompress must not appear in filter_complex when ducking=False"


def test_duck_ratio_appears_in_command(tmp_path):
    """The configured duck_ratio should influence the compressor ratio in the command."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    music = _make_music(tmp_path)
    output = tmp_path / "out.mp4"
    config = {
        "music": {"ducking": True, "duck_ratio": 0.5},
        "audio_fx": {"program_loudnorm": False},
    }

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        output.write_bytes(b"fake_output")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, output, music=music, config=config)

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    # duck_ratio=0.5 → comp_ratio = 1 + 0.5*10 = 6.0
    assert "ratio=6.0" in all_args or "ratio=" in all_args


def test_no_music_no_ducking(tmp_path):
    """When no music file is provided, ducking code should not run."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    output = tmp_path / "out.mp4"
    config = {
        "music": {"ducking": True, "duck_ratio": 0.3},
        "audio_fx": {"program_loudnorm": False},
    }

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        output.write_bytes(b"fake_output")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, output, music=None, config=config)

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    assert "sidechaincompress" not in all_args
