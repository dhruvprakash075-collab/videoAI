"""Tests for utils.preflight."""

import time
from unittest.mock import patch

from utils.preflight import (
    PreflightCheck,
    PreflightResult,
    _check_disk,
    _check_ffmpeg,
    _check_python,
    _timed,
    run_preflight,
)


class TestPreflightCheck:
    def test_status_is_ok(self):
        c = PreflightCheck(name="x", status="ok", message="fine")
        assert c.is_ok is True
        assert c.is_fail is False

    def test_status_is_fail(self):
        c = PreflightCheck(name="x", status="fail", message="broken")
        assert c.is_ok is False
        assert c.is_fail is True


class TestPreflightResult:
    def test_all_ok_when_no_failures(self):
        r = PreflightResult(checks=[
            PreflightCheck(name="a", status="ok"),
            PreflightCheck(name="b", status="warn"),
        ])
        assert r.all_ok is True
        assert r.failures == []
        assert len(r.warnings) == 1

    def test_not_all_ok_when_failure(self):
        r = PreflightResult(checks=[
            PreflightCheck(name="a", status="ok"),
            PreflightCheck(name="b", status="fail"),
        ])
        assert r.all_ok is False
        assert len(r.failures) == 1

    def test_summary_counts(self):
        r = PreflightResult(checks=[
            PreflightCheck(name="a", status="ok"),
            PreflightCheck(name="b", status="ok"),
            PreflightCheck(name="c", status="warn"),
            PreflightCheck(name="d", status="fail"),
            PreflightCheck(name="e", status="skip"),
        ])
        assert r.summary() == "ok=2 warn=1 fail=1 skip=1"


class TestTimed:
    def test_captures_duration(self):
        def slow():
            time.sleep(0.01)
            return "ok", "done"

        c = _timed(slow, name="slow_check")
        assert c.name == "slow_check"
        assert c.status == "ok"
        assert c.duration_ms >= 10

    def test_captures_exception_as_fail(self):
        def boom():
            raise RuntimeError("nope")

        c = _timed(boom, name="boom_check")
        assert c.status == "fail"
        assert "nope" in c.message


class TestPythonCheck:
    def test_python_passes(self):
        status, msg = _check_python()
        assert status == "ok"
        assert "Python" in msg


class TestFfmpegCheck:
    def test_ffmpeg_returns_some_status(self):
        status, msg = _check_ffmpeg()
        assert status in {"ok", "warn", "fail"}
        assert msg


class TestDiskCheck:
    def test_disk_check_uses_video_output_dir(self):
        config = {"video": {"output_path": "studio_outputs/final_video.mp4"}}
        status, msg = _check_disk(config)  # type: ignore[arg-type]
        # Windows: C: drive. Status should be ok/warn/fail, not skip.
        assert status in {"ok", "warn", "fail"}
        assert "GB free" in msg


class TestRunPreflight:
    def test_returns_result_object(self):
        result = run_preflight(config={}, quiet=True)
        assert isinstance(result, PreflightResult)
        # Should have at least python + ffmpeg checks. Ollama/VRAM/disk may
        # be ok/warn/fail/skip depending on environment.
        assert len(result.checks) >= 2

    def test_quiet_does_not_print(self, capsys):
        run_preflight(config={}, quiet=True)
        captured = capsys.readouterr()
        assert "Preflight" not in captured.out

    def test_non_quiet_prints_summary(self, capsys):
        run_preflight(config={}, quiet=False)
        captured = capsys.readouterr()
        assert "Preflight" in captured.out

    def test_fail_fast_stops_on_first_failure(self):
        """If a check fails, fail_fast should return after that one."""
        # Make the FIRST check (python) fail so we can prove fail_fast halts.
        with patch("utils.preflight._check_python", return_value=("fail", "wrong python")):
            result = run_preflight(config={}, fail_fast=True, quiet=True)
            # Only the python check should have run, since it's first.
            assert len(result.checks) == 1
            assert result.checks[0].name == "python"
            assert result.checks[0].status == "fail"
