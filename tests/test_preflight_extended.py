"""test_preflight_extended.py - Extended unit tests for utils/preflight.py"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.preflight import (
    _check_director_model,
    _check_disk,
    _check_ffmpeg,
    _check_ollama,
    _check_python,
    _check_vram,
    _format_report,
    main,
    run_preflight,
)


def test_check_ollama_reachable():
    class FakeResp:
        def read(self):
            return b'{"models": [{"name": "hermes-director:latest"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        status, msg = _check_ollama({})
        assert status == "ok"
        assert "reachable" in msg


def test_check_ollama_unreachable():
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
        status, msg = _check_ollama({})
        assert status == "fail"
        assert "Cannot reach Ollama" in msg


def test_check_ollama_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("unknown error")):
        status, msg = _check_ollama({})
        assert status == "fail"
        assert "Ollama probe failed" in msg


def test_check_director_model_found():
    class FakeResp:
        def read(self):
            return b'{"models": [{"name": "hermes-director:latest"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        status, _msg = _check_director_model({"models": {"director": "hermes-director"}})
        assert status == "ok"


def test_check_director_model_not_found():
    class FakeResp:
        def read(self):
            return b'{"models": [{"name": "qwen2.5:0.5b"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        status, _msg = _check_director_model({"models": {"director": "hermes-director"}})
        assert status == "fail"


def test_check_director_model_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        status, _msg = _check_director_model({})
        assert status == "warn"


def test_check_vram_skip_no_torch(monkeypatch):
    # Hide torch if it's imported
    monkeypatch.setitem(sys.modules, "torch", None)
    status, msg = _check_vram({})
    assert status == "skip"
    assert "torch not installed" in msg


def test_check_vram_no_cuda(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    status, msg = _check_vram({})
    assert status == "skip"
    assert "no CUDA GPU detected" in msg


def test_check_vram_ok(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    # Return 6GB free, 8GB total
    mock_torch.cuda.mem_get_info.return_value = (6 * 1024**3, 8 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    status, msg = _check_vram({"performance": {"vram_sd_threshold_gb": 4.5}})
    assert status == "ok"
    assert "6.0/8.0 GB free" in msg


def test_check_vram_fail(monkeypatch):
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    # Return 2GB free, 8GB total
    mock_torch.cuda.mem_get_info.return_value = (2 * 1024**3, 8 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", mock_torch)

    status, msg = _check_vram({"performance": {"vram_sd_threshold_gb": 4.5}})
    assert status == "fail"
    assert "only 2.0/8.0 GB free" in msg


def test_check_disk_fallback_parent():
    mock_usage = MagicMock()
    mock_usage.free = 10 * 1024**3
    with (
        patch("psutil.disk_usage", return_value=mock_usage),
        patch("pathlib.Path.exists", return_value=False),
    ):
        status, _msg = _check_disk({"video": {"output_path": "nonexistent_dir/output.mp4"}})
        assert status == "ok"


def test_check_disk_warn():
    mock_usage = MagicMock()
    # 2GB free
    mock_usage.free = 2 * 1024**3
    with patch("psutil.disk_usage", return_value=mock_usage):
        status, msg = _check_disk({})
        assert status == "warn"
        assert "only 2.0 GB free" in msg


def test_check_disk_fail():
    mock_usage = MagicMock()
    # 0.5GB free
    mock_usage.free = int(0.5 * 1024**3)
    with patch("psutil.disk_usage", return_value=mock_usage):
        status, msg = _check_disk({})
        assert status == "fail"
        assert "only 0.5 GB free" in msg


def test_check_ffmpeg_missing():
    with patch("shutil.which", return_value=None):
        status, msg = _check_ffmpeg()
        assert status == "fail"
        assert "ffmpeg not found" in msg


def test_check_ffmpeg_timeout():
    with (
        patch("shutil.which", return_value="ffmpeg"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 5)),
    ):
        status, msg = _check_ffmpeg()
        assert status == "warn"
        assert "probe timed out" in msg


def test_check_ffmpeg_exception():
    with (
        patch("shutil.which", return_value="ffmpeg"),
        patch("subprocess.run", side_effect=RuntimeError("unknown error")),
    ):
        status, msg = _check_ffmpeg()
        assert status == "warn"
        assert "failed: unknown error" in msg


def test_check_python_fail(monkeypatch):
    class FakeVersionInfo:
        major = 3
        minor = 9
        micro = 5

    monkeypatch.setattr(sys, "version_info", FakeVersionInfo())
    status, msg = _check_python()
    assert status == "fail"
    assert "not supported" in msg


def test_run_preflight_no_config():
    with (
        patch("utils.preflight._check_python", return_value=("ok", "python ok")),
        patch("utils.preflight._check_ollama", return_value=("ok", "ollama ok")),
        patch("utils.preflight._check_director_model", return_value=("ok", "model ok")),
        patch("utils.preflight._check_vram", return_value=("ok", "vram ok")),
        patch("utils.preflight._check_disk", return_value=("ok", "disk ok")),
        patch("utils.preflight._check_ffmpeg", return_value=("ok", "ffmpeg ok")),
    ):
        result = run_preflight(config=None, quiet=True)
        assert result.all_ok


def test_format_report_outputs():
    # Test layout with failures
    from utils.preflight import PreflightCheck, PreflightResult

    r = PreflightResult()
    r.checks = [
        PreflightCheck("python", "ok", "Python 3.12.13"),
        PreflightCheck("disk_space", "warn", "low space"),
        PreflightCheck("ollama_ping", "fail", "connection refused"),
    ]
    report = _format_report(r)
    assert "[OK]" in report
    assert "[WARN]" in report
    assert "[FAIL]" in report
    assert "FAILED" in report

    # Test layout with warnings only
    r2 = PreflightResult()
    r2.checks = [
        PreflightCheck("python", "ok", "Python 3.12.13"),
        PreflightCheck("disk_space", "warn", "low space"),
    ]
    report2 = _format_report(r2)
    assert "OK with warnings" in report2

    # Test layout all ok
    r3 = PreflightResult()
    r3.checks = [
        PreflightCheck("python", "ok", "Python 3.12.13"),
    ]
    report3 = _format_report(r3)
    assert "OK -- pipeline is ready" in report3


def test_main_ok():
    with (
        patch("config.load_config", return_value={}),
        patch("utils.preflight.run_preflight") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.all_ok = True
        mock_run.return_value = mock_result
        exit_code = main()
        assert exit_code == 0


def test_main_fail():
    with (
        patch("config.load_config", return_value={}),
        patch("utils.preflight.run_preflight") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.all_ok = False
        mock_run.return_value = mock_result
        exit_code = main()
        assert exit_code == 1


def test_main_config_load_fail():
    # Trigger config load exception warning branch
    with (
        patch("config.load_config", side_effect=Exception("parse error")),
        patch("utils.preflight.run_preflight") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.all_ok = True
        mock_run.return_value = mock_result
        exit_code = main()
        assert exit_code == 0
