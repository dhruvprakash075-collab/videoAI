import itertools
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from video.image_gen.comfyui_runtime import ComfyUIRuntime, get_comfyui_runtime


def _runtime(tmp_path: Path, **cfg) -> ComfyUIRuntime:
    root = tmp_path / "ComfyUI"
    root.mkdir(exist_ok=True)
    py = root / "python.exe"
    py.write_text("", encoding="utf-8")
    return ComfyUIRuntime(
        {
            "comfyui": {
                "host": "127.0.0.1",
                "port": 8188,
                "root": str(root),
                "python": str(py),
                **cfg,
            }
        }
    )


def test_resolve_path_variants_and_factory(tmp_path: Path):
    runtime = _runtime(tmp_path)
    absolute = tmp_path / "abs.txt"
    absolute.write_text("x", encoding="utf-8")
    assert runtime._resolve_path(str(absolute)) == absolute

    base = tmp_path / "base"
    base.mkdir()
    child = base / "child.txt"
    child.write_text("x", encoding="utf-8")
    assert runtime._resolve_path("child.txt", base=base, require_file=True) == child
    assert runtime._resolve_path("missing.txt", require_file=True) == Path("missing.txt")
    assert runtime._resolve_path("missing-dir") == runtime._project_root / "missing-dir"
    assert get_comfyui_runtime({"comfyui": {"host": "127.0.0.1"}}).base_url.startswith(
        "http://127.0.0.1:"
    )


def test_log_handles_close_even_when_close_raises(tmp_path: Path):
    runtime = _runtime(tmp_path)
    out, err = runtime._open_log_handles(Path(runtime.root))
    assert out is runtime._stdout_handle
    assert err is runtime._stderr_handle
    runtime._close_log_handles()
    assert runtime._stdout_handle is None
    assert runtime._stderr_handle is None

    bad = MagicMock()
    bad.close.side_effect = RuntimeError("ignored")
    runtime._stdout_handle = bad
    runtime._close_log_handles()
    assert runtime._stdout_handle is None


def test_is_running_false_for_http_error(tmp_path: Path):
    import urllib.error

    runtime = _runtime(tmp_path)
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("", 500, "", {}, None)):
        assert runtime.is_running() is False


def test_is_running_false_for_non_200(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch("urllib.request.urlopen") as mock_urlopen:
        response = MagicMock(status=204)
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response
        assert runtime.is_running() is False


def test_ensure_running_autostarts(tmp_path: Path):
    runtime = _runtime(tmp_path, auto_start=True)
    with (
        patch.object(runtime, "is_running", return_value=False),
        patch.object(runtime, "start", return_value=True) as start,
    ):
        assert runtime.ensure_running(timeout=3) is True
    start.assert_called_once_with(timeout=3)


def test_ensure_running_already_running_and_disabled(tmp_path: Path):
    runtime = _runtime(tmp_path, auto_start=True)
    with patch.object(runtime, "is_running", return_value=True), patch.object(runtime, "start") as start:
        assert runtime.ensure_running(timeout=3) is True
    start.assert_not_called()

    runtime = _runtime(tmp_path, auto_start=False)
    with patch.object(runtime, "is_running", return_value=False):
        assert runtime.ensure_running(timeout=3) is False


def test_start_success_permission_retry_failure_and_timeout(tmp_path: Path):
    runtime = _runtime(tmp_path)
    proc = MagicMock(pid=123)
    with (
        patch.object(runtime, "is_running", side_effect=[False, True]),
        patch("subprocess.Popen", return_value=proc) as popen,
        patch("time.sleep"),
    ):
        assert runtime.start(timeout=2) is True
    assert popen.called

    runtime = _runtime(tmp_path)
    proc = MagicMock(pid=456)
    with (
        patch.object(runtime, "is_running", side_effect=[False, True]),
        patch("subprocess.Popen", side_effect=[PermissionError("denied"), proc]) as popen,
        patch("time.sleep"),
    ):
        assert runtime.start(timeout=2) is True
    assert popen.call_count == 2

    runtime = _runtime(tmp_path)
    with patch("subprocess.Popen", side_effect=RuntimeError("boom")):
        assert runtime.start(timeout=0) is False
        assert runtime._process is None

    runtime = _runtime(tmp_path)
    fake_clock = itertools.count().__next__
    with (
        patch.object(runtime, "is_running", return_value=False),
        patch("subprocess.Popen", return_value=MagicMock(pid=789)),
        patch("time.time", side_effect=fake_clock),
        patch("time.sleep"),
    ):
        assert runtime.start(timeout=1) is False


def test_start_already_process_and_stop_paths(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime._process = MagicMock()
    assert runtime.start() is True

    proc = MagicMock(pid=1)
    runtime._process = proc
    runtime.stop()
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=10)
    assert runtime._process is None

    proc = MagicMock(pid=2)
    proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 10)
    runtime._process = proc
    runtime.stop()
    proc.kill.assert_called_once()

    proc = MagicMock(pid=3)
    proc.terminate.side_effect = RuntimeError("ignored")
    runtime._process = proc
    runtime.stop()
    assert runtime._process is None
