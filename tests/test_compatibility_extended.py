"""test_compatibility_extended.py - Unit tests for utils/compatibility.py"""

import sys
from unittest.mock import MagicMock, patch

from utils import compatibility


def test_setup_compatibility_win32():
    with (
        patch("sys.platform", "win32"),
        patch("sys.stdout") as mock_stdout,
        patch("sys.stderr") as mock_stderr,
    ):
        compatibility.setup_compatibility()
        mock_stdout.reconfigure.assert_called_with(encoding="utf-8")
        mock_stderr.reconfigure.assert_called_with(encoding="utf-8")


def test_setup_compatibility_win32_exception():
    mock_stdout = MagicMock()
    mock_stdout.reconfigure.side_effect = OSError("Access denied")

    with patch("sys.platform", "win32"), patch("sys.stdout", mock_stdout):
        # Should catch exception and pass
        compatibility.setup_compatibility()


def test_setup_compatibility_non_win32():
    with patch("sys.platform", "darwin"), patch("sys.stdout") as mock_stdout:
        compatibility.setup_compatibility()
        mock_stdout.reconfigure.assert_not_called()


def test_check_dependencies():
    # Test when all packages exist vs missing packages
    with patch("builtins.__import__", return_value=MagicMock()):
        missing = compatibility.check_dependencies()
        assert isinstance(missing, list)

    # Test when import fails (missing package). PEFT is checked by spec because
    # importing it can initialize heavy optional Torch paths on Windows.
    def fake_import(name, *args, **kwargs):
        if name == "crewai":
            raise ImportError("module not found")
        return MagicMock()

    with (
        patch("builtins.__import__", side_effect=fake_import),
        patch("utils.compatibility.find_spec", side_effect=lambda name: None if name == "peft" else MagicMock()),
    ):
        missing = compatibility.check_dependencies()
        assert "peft" in missing
        assert "crewai" in missing


def test_apply_all_patches():
    # Reset flag so it runs
    if hasattr(sys, "_video_ai_compat_applied"):
        del sys._video_ai_compat_applied

    with (
        patch("utils.compatibility.setup_compatibility") as mock_setup,
        patch(
            "utils.compatibility.check_dependencies", return_value=["missing-pkg"]
        ) as _mock_check,
        patch("logging.Logger.warning") as mock_warn,
    ):
        compatibility.apply_all_patches()
        mock_setup.assert_called_once()
        mock_warn.assert_called()

    # Call again, should skip
    with patch("utils.compatibility.setup_compatibility") as mock_setup:
        compatibility.apply_all_patches()
        mock_setup.assert_not_called()


def test_setup_compatibility_no_reconfigure():
    # Setup sys.stdout and sys.stderr without reconfigure attribute
    mock_stdout = object()  # bare object has no attributes/reconfigure
    mock_stderr = object()
    with (
        patch("sys.platform", "win32"),
        patch("sys.stdout", mock_stdout),
        patch("sys.stderr", mock_stderr),
    ):
        compatibility.setup_compatibility()  # should not crash


def test_check_dependencies_cuda_not_available():
    # Mock torch to have cuda.is_available returning False
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return mock_torch
        return MagicMock()

    with (
        patch("builtins.__import__", side_effect=fake_import),
        patch("utils.compatibility.log") as mock_log,
    ):
        compatibility.check_dependencies()
        mock_log.warning.assert_any_call("CUDA not available — image generation will be slow")
