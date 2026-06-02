"""test_assembler_loudnorm.py - Tests for A3: 2-pass loudnorm in concatenate_segments."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch


def _make_segments(tmp_path, n=2):
    segs = []
    for i in range(n):
        p = tmp_path / f"seg_{i:02d}.mp4"
        p.write_bytes(b"fake")
        segs.append(p)
    return segs


def test_loudnorm_disabled_no_extra_ffmpeg_call(tmp_path):
    """When program_loudnorm is false, only one ffmpeg call (the concat)."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    output = tmp_path / "out.mp4"
    config = {"audio_fx": {"program_loudnorm": False}}

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        output.write_bytes(b"fake_output")

    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, output, config=config)

    # Only one _run call (the concat itself)
    assert len(run_calls) == 1
    # No loudnorm filter in the command — check only the filter args, not paths
    cmd = run_calls[0]
    for j, arg in enumerate(cmd):
        if str(arg) in ("-af", "-filter_complex"):
            assert "loudnorm" not in str(cmd[j + 1]), \
                "loudnorm should not appear in filter arg when program_loudnorm=False"


def test_loudnorm_enabled_two_pass_present(tmp_path):
    """When program_loudnorm is true, the apply pass must include linear=true."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    output = tmp_path / "out.mp4"
    config = {"audio_fx": {"program_loudnorm": True, "target_lufs": -14}}

    # Fake measured values in stderr JSON
    _fake_stderr = (
        '{"input_i": "-18.5", "input_tp": "-1.2", "input_lra": "8.0", '
        '"input_thresh": "-28.5", "target_offset": "0.5"}'
    )

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        # Create the temp file so the loudnorm branch finds it
        for arg in cmd:
            if str(arg).endswith(".mp4") and "_prenorm_" in str(arg):
                Path(arg).write_bytes(b"fake_prenorm")
        if str(output) in [str(a) for a in cmd]:
            output.write_bytes(b"fake_output")

    fake_proc = MagicMock()
    fake_proc.stderr = _fake_stderr
    fake_proc.returncode = 0

    with patch("video.renderer.assembler._run", side_effect=fake_run), \
         patch("subprocess.run", return_value=fake_proc):
        concatenate_segments(segs, output, config=config)

    # At least 2 _run calls: concat + loudnorm apply
    assert len(run_calls) >= 2
    # The apply pass must contain linear=true
    apply_cmd = " ".join(str(a) for a in run_calls[-1])
    assert "linear=true" in apply_cmd or "loudnorm" in apply_cmd


def test_loudnorm_target_lufs_used(tmp_path):
    """The target LUFS value from config must appear in the loudnorm filter."""
    from video.renderer.assembler import concatenate_segments
    segs = _make_segments(tmp_path)
    output = tmp_path / "out.mp4"
    config = {"audio_fx": {"program_loudnorm": True, "target_lufs": -16}}

    _fake_stderr = (
        '{"input_i": "-20.0", "input_tp": "-2.0", "input_lra": "7.0", '
        '"input_thresh": "-30.0", "target_offset": "0.0"}'
    )

    run_calls = []
    def fake_run(cmd, timeout=300):
        run_calls.append(cmd)
        for arg in cmd:
            if str(arg).endswith(".mp4") and "_prenorm_" in str(arg):
                Path(arg).write_bytes(b"fake_prenorm")
        if str(output) in [str(a) for a in cmd]:
            output.write_bytes(b"fake_output")

    fake_proc = MagicMock()
    fake_proc.stderr = _fake_stderr
    fake_proc.returncode = 0

    with patch("video.renderer.assembler._run", side_effect=fake_run), \
         patch("subprocess.run", return_value=fake_proc):
        concatenate_segments(segs, output, config=config)

    all_args = " ".join(str(a) for cmd in run_calls for a in cmd)
    assert "-16" in all_args, "Target LUFS -16 should appear in FFmpeg args"
