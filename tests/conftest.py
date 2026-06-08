"""
conftest.py - Shared pytest fixtures for the Video.AI test suite.

UIState uses class-level (global) state shared across the TUI, the FastAPI
dashboard, and the pipeline thread. Without a reset between tests, state bleeds
from one test into the next. The autouse fixture below resets EVERY UIState
field before each test so tests are isolated and order-independent.
"""

import sys
import threading
import types
from pathlib import Path

import pytest

# Make the repo root importable (tests/ is one level down)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _install_optional_dependency_stubs() -> None:
    """Make optional heavyweight packages importable for monkeypatch-only tests."""
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
    yield


@pytest.fixture
def tmp_root(tmp_path):
    """A temporary directory root for store/blackboard tests.

    test_blackboard.py and test_project_store.py construct Blackboard/ProjectStore/
    StoryStore with an explicit root directory. This fixture provides a fresh,
    isolated temp directory per test (delegates to pytest's built-in tmp_path).
    """
    return tmp_path
