"""
conftest.py - Shared pytest fixtures for the Video.AI test suite.

UIState uses class-level (global) state shared across the TUI, the FastAPI
dashboard, and the pipeline thread. Without a reset between tests, state bleeds
from one test into the next. The autouse fixture below resets EVERY UIState
field before each test so tests are isolated and order-independent.
"""

import sys
import threading
from pathlib import Path

import pytest

# Make the repo root importable (tests/ is one level down)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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
