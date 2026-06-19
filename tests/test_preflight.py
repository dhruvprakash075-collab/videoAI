"""Tests for utils.preflight."""

import time
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from utils.preflight import (
    PreflightCheck,
    PreflightResult,
    _check_disk,
    _check_ffmpeg,
    _check_python,
    _check_qwen_edit,
    _timed,
    run_preflight,
)


@contextmanager
def _mock_side_effecting_preflight_checks():
    """ponytail: aggregate preflight tests must not touch local services/hardware."""
    checks = [
        ("utils.preflight._check_ollama", ("ok", "mocked")),
        ("utils.preflight._check_director_model", ("ok", "mocked")),
        ("utils.preflight._check_vram", ("skip", "mocked")),
        ("utils.preflight._check_disk", ("ok", "mocked")),
        ("utils.preflight._check_supertonic_voice", ("skip", "mocked")),
        ("utils.preflight._check_layered_v3", ("skip", "mocked")),
        ("utils.preflight._check_qwen_edit", ("skip", "mocked")),
        ("utils.preflight._check_ffmpeg", ("ok", "mocked")),
        ("utils.preflight._check_playwright", ("skip", "mocked")),
    ]
    with ExitStack() as stack:
        for target, result in checks:
            stack.enter_context(patch(target, return_value=result))
        yield


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
        r = PreflightResult(
            checks=[
                PreflightCheck(name="a", status="ok"),
                PreflightCheck(name="b", status="warn"),
            ]
        )
        assert r.all_ok is True
        assert r.failures == []
        assert len(r.warnings) == 1

    def test_not_all_ok_when_failure(self):
        r = PreflightResult(
            checks=[
                PreflightCheck(name="a", status="ok"),
                PreflightCheck(name="b", status="fail"),
            ]
        )
        assert r.all_ok is False
        assert len(r.failures) == 1

    def test_summary_counts(self):
        r = PreflightResult(
            checks=[
                PreflightCheck(name="a", status="ok"),
                PreflightCheck(name="b", status="ok"),
                PreflightCheck(name="c", status="warn"),
                PreflightCheck(name="d", status="fail"),
                PreflightCheck(name="e", status="skip"),
            ]
        )
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


class TestQwenEditCheck:
    def test_qwen_edit_skips_when_disabled(self):
        status, msg = _check_qwen_edit(
            {"image_gen": {"composition_mode": "one_pass", "qwen_edit": {"enabled": False}}}
        )
        assert status == "skip"
        assert "disabled" in msg

    def test_qwen_edit_warns_on_missing_items(self):
        with patch(
            "video.image_gen.qwen_repose.preflight_qwen_edit",
            return_value=["qwen_edit.model_path is empty"],
        ):
            status, msg = _check_qwen_edit(
                {"image_gen": {"composition_mode": "qwen_edit", "qwen_edit": {"enabled": True}}}
            )
        assert status == "warn"
        assert "model_path" in msg

    def test_qwen_edit_passes_when_preflight_clear(self):
        with patch("video.image_gen.qwen_repose.preflight_qwen_edit", return_value=[]):
            status, msg = _check_qwen_edit(
                {"image_gen": {"composition_mode": "qwen_edit", "qwen_edit": {"enabled": True}}}
            )
        assert status == "ok"
        assert "passed" in msg


class TestRunPreflight:
    def test_returns_result_object(self):
        with _mock_side_effecting_preflight_checks():
            result = run_preflight(config={}, quiet=True)
        assert isinstance(result, PreflightResult)
        assert len(result.checks) >= 2

    def test_quiet_does_not_print(self, capsys):
        with _mock_side_effecting_preflight_checks():
            run_preflight(config={}, quiet=True)
        captured = capsys.readouterr()
        assert "Preflight" not in captured.out

    def test_non_quiet_prints_summary(self, capsys):
        with _mock_side_effecting_preflight_checks():
            run_preflight(config={}, quiet=False)
        captured = capsys.readouterr()
        assert "Preflight" in captured.out

    def test_fail_fast_stops_on_first_failure(self):
        """If a check fails, fail_fast should return after that one."""
        with patch("utils.preflight._check_python", return_value=("fail", "wrong python")):
            result = run_preflight(config={}, fail_fast=True, quiet=True)
            assert len(result.checks) == 1
            assert result.checks[0].name == "python"
            assert result.checks[0].status == "fail"

    def test_includes_qwen_edit_check(self):
        with _mock_side_effecting_preflight_checks():
            result = run_preflight(config={}, quiet=True)
        assert any(check.name == "qwen_edit" for check in result.checks)
