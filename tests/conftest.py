"""
conftest.py - Shared pytest fixtures for the Video.AI test suite.

UIState uses class-level (global) state shared across the TUI, the FastAPI
dashboard, and the pipeline thread. Without a reset between tests, state bleeds
from one test into the next. The autouse fixture below resets EVERY UIState
field before each test so tests are isolated and order-independent.
"""

import contextlib
import os
import sys
import threading
import types
from pathlib import Path

import _pytest.pathlib as _pp
import pytest

# ponytail: prevent manual live tests from being collected
collect_ignore = ["manual_integration_test.py", "manual_integration_test_b.py"]


def pytest_addoption(parser):
    parser.addoption(
        "--run-smoke", action="store_true", default=False, help="run smoke tests"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: mark test as smoke test (ComfyUI integration)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-smoke"):
        skip_smoke = pytest.mark.skip(reason="need --run-smoke option to run")
        for item in items:
            if "smoke" in item.keywords:
                item.add_marker(skip_smoke)


# Disable CUDA globally in tests to prevent GPU driver crashes on low-VRAM
# cards (RTX 4050 6GB). Many source files do `import torch` inside functions,
# which initializes CUDA (~500MB-1GB VRAM). Tests don't need real CUDA — they
# all patch/mock torch.cuda.*. Set before any torch import can happen.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

# Suppress pyarrow C++ shutdown crash on Windows (DLL unloading order).
# The pyarrow Windows wheel triggers an access violation at process exit when
# native DLLs are unloaded in the wrong order. This flag tells pyarrow to skip
# its C++ atexit handler, avoiding the crash without affecting functionality.
os.environ.setdefault("PYARROW_IGNORE_CPP_SHUTDOWN", "1")

# Suppress pytest's cleanup_numbered_dir PermissionError at exit on Windows.
# This is a known pytest-on-Windows bug: atexit cleanup of numbered temp dirs
# fails with [WinError 5] when symlinks are still locked. The tests all pass;
# the traceback is noise.
_orig_cleanup = _pp.cleanup_numbered_dir


def _safe_cleanup_numbered_dir(root, prefix, keep, consider_lock_dead_if_created_before):
    with contextlib.suppress(PermissionError, OSError):
        _orig_cleanup(root, prefix, keep, consider_lock_dead_if_created_before)


_pp.cleanup_numbered_dir = _safe_cleanup_numbered_dir

# Make the repo root importable (tests/ is one level down)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_COMFYUI_NODES = _ROOT / "comfyui_nodes"
if _COMFYUI_NODES.is_dir() and str(_COMFYUI_NODES) not in sys.path:
    sys.path.insert(0, str(_COMFYUI_NODES))


def _install_optional_dependency_stubs() -> None:
    """Make optional heavyweight packages importable for monkeypatch-only tests."""
    # Stub pyarrow to avoid loading native DLLs (causes Windows access violation at shutdown).
    if "pyarrow" not in sys.modules:
        sys.modules["pyarrow"] = types.ModuleType("pyarrow")
        sys.modules["pyarrow"].__version__ = "24.0.0"
        sys.modules["pyarrow"].lib = types.ModuleType("pyarrow.lib")
        sys.modules["pyarrow"].fs = types.ModuleType("pyarrow.fs")
        sys.modules["pyarrow"].parquet = types.ModuleType("pyarrow.parquet")
        sys.modules["pyarrow"].dataset = types.ModuleType("pyarrow.dataset")
        sys.modules["pyarrow"].compute = types.ModuleType("pyarrow.compute")

    if "crewai" not in sys.modules:
        crewai = types.ModuleType("crewai")

        class _LLM:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.model = kwargs.get("model") if kwargs else None

        class _Agent:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.llm = kwargs.get("llm") if kwargs else None

        class _Crew:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def kickoff(self):
                return ""

        class _Task:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        crewai.LLM = _LLM
        crewai.Agent = _Agent
        crewai.Crew = _Crew
        crewai.Task = _Task
        sys.modules["crewai"] = crewai

    if "crewai.process" not in sys.modules:
        process = types.ModuleType("crewai.process")

        class _Process:
            sequential = "sequential"

        process.Process = _Process
        sys.modules["crewai.process"] = process

    if "faster_whisper" not in sys.modules:
        faster_whisper = types.ModuleType("faster_whisper")

        class _WhisperModel:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        faster_whisper.WhisperModel = _WhisperModel
        sys.modules["faster_whisper"] = faster_whisper

    if "whisper" not in sys.modules:
        whisper = types.ModuleType("whisper")

        def _load_model(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        whisper.load_model = _load_model
        sys.modules["whisper"] = whisper


_install_optional_dependency_stubs()


@pytest.fixture(autouse=True)
def reset_uistate():
    """Reset ALL UIState class attributes before each test (prevents bleed)."""
    from agents.director_agent import UIState

    UIState.is_ui_mode = False
    UIState.pause_event = threading.Event()
    UIState.active_question = None
    UIState.user_reply = None
    UIState.status = "running"
    UIState.logs = []
    UIState.topic = ""
    UIState.character = "narrator"
    UIState.output_video = ""
    UIState.current_script = ""
    # Phase 1 additive fields
    UIState.segment_current = 0
    UIState.segment_total = 0
    UIState.run_start_ts = 0.0
    UIState.vram_text = ""
    # A6 additive field
    UIState.auto_accept = False
    # B2 additive field — degradation ledger (reset so it never bleeds between tests)
    UIState.degradations = []
    # Phase 0 manifest tracking
    UIState.run_id = ""
    UIState.vram_peaks = []
    UIState.warning_count = 0
    UIState.segment_manifests = {}
    yield


@pytest.fixture
def tmp_root(tmp_path):
    """A temporary directory root for store/blackboard tests.

    test_blackboard.py and test_project_store.py construct Blackboard/ProjectStore/
    StoryStore with an explicit root directory. This fixture provides a fresh,
    isolated temp directory per test (delegates to pytest's built-in tmp_path).
    """
    return tmp_path
