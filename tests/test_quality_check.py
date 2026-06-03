"""test_quality_check.py - check_video against mock ffprobe output."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from utils.quality_check import check_video


def _probe_payload(duration=12.0, width=1920, height=1080, has_audio=True):
    streams = [{"codec_type": "video", "width": width, "height": height}]
    if has_audio:
        streams.append({"codec_type": "audio"})
    return {"format": {"duration": str(duration)}, "streams": streams}


def test_missing_file_fails(tmp_path: Path):
    out = check_video(tmp_path / "missing.mp4", {"video": {"resolution": "1920x1080"}})
    assert out["passed"] is False
    assert "File not found" in out["issues"]


def test_file_too_small(tmp_path: Path):
    p = tmp_path / "tiny.mp4"
    p.write_bytes(b"x" * 1024)  # ~0KB
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(_probe_payload()), stderr=""
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 1}})
    assert any("too small" in i for i in out["issues"])


def test_valid_passes(tmp_path: Path):
    p = tmp_path / "ok.mp4"
    p.write_bytes(b"x" * 1024 * 200)  # ~0.2MB
    with patch("subprocess.run") as run_mock:
        # 60s actual, 60s expected (1 min * 60s), within 20% tolerance
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(_probe_payload(duration=60.0)), stderr=""
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 1}})
    assert out["passed"] is True
    assert out["issues"] == []
    assert out["details"]["width"] == 1920


def test_ffprobe_error(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ffprobe: invalid data"
        )
        out = check_video(p, {"video": {"resolution": "1920x1080"}})
    assert out["passed"] is False
    assert any("ffprobe error" in i for i in out["issues"])


def test_ffprobe_timeout(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)):
        out = check_video(p, {"video": {"resolution": "1920x1080"}})
    assert any("timeout" in i for i in out["issues"])


def test_ffprobe_invalid_json(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        out = check_video(p, {"video": {"resolution": "1920x1080"}})
    assert any("invalid JSON" in i for i in out["issues"])


def test_no_audio_stream(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(_probe_payload(has_audio=False, duration=12.0)),
            stderr="",
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 1}})
    assert any("No audio stream" in i for i in out["issues"])


def test_no_video_stream(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"format": {"duration": "12"}, "streams": [{"codec_type": "audio"}]}),
            stderr="",
        )
        out = check_video(p, {"video": {"resolution": "1920x1080"}})
    assert any("No video stream" in i for i in out["issues"])


def test_duration_mismatch(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        # 12s actual, but expected 600s
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(_probe_payload(duration=12.0)), stderr=""
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 10}})
    assert any("Duration mismatch" in i for i in out["issues"])


def test_resolution_mismatch(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(_probe_payload(duration=12.0, width=1280, height=720)),
            stderr="",
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 1}})
    assert any("Resolution" in i for i in out["issues"])


def test_expected_duration_overrides_config(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(_probe_payload(duration=15.0)), stderr=""
        )
        out = check_video(
            p,
            {"video": {"resolution": "1920x1080", "total_duration_min": 1}},
            expected_duration_s=15.0,
        )
    assert out["passed"] is True


def test_invalid_resolution_format(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(_probe_payload(duration=12.0)), stderr=""
        )
        out = check_video(p, {"video": {"resolution": "FHD", "total_duration_min": 1}})
    assert any("Invalid resolution" in i for i in out["issues"])


def test_duration_na_from_ffprobe(tmp_path: Path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x" * 200000)
    with patch("subprocess.run") as run_mock:
        # ffprobe returns N/A for duration on some containers
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"format": {"duration": "N/A"}, "streams": _probe_payload()["streams"]}
            ),
            stderr="",
        )
        out = check_video(p, {"video": {"resolution": "1920x1080", "total_duration_min": 1}})
    assert any("N/A" in i or "duration" in i.lower() for i in out["issues"])
