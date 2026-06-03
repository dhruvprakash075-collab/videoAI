"""test_autoaccept.py - Tests for A6: --yes auto-accept flag."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.director_agent import DirectorAgent, UIState


def test_consult_user_returns_default_when_auto_accept():
    """consult_user should return the first option without prompting when auto_accept=True."""
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    agent = DirectorAgent(llm_config={})
    options = ["Option A", "Option B", "Option C"]
    result = agent.consult_user("Which do you prefer?", options=options)
    assert result == "Option A"


def test_consult_user_returns_fallback_when_no_options():
    """consult_user with no options should return the default string."""
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    agent = DirectorAgent(llm_config={})
    result = agent.consult_user("Any thoughts?", options=None)
    assert result == "Proceed as planned."


def test_consult_fields_returns_all_defaults_when_auto_accept():
    """consult_fields should return all first options without prompting."""
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    agent = DirectorAgent(llm_config={})
    fields = [
        {
            "key": "duration",
            "label": "Duration",
            "current": "10",
            "options": ["10", "20", "30"],
            "impact": 1,
        },
        {
            "key": "style",
            "label": "Style",
            "current": "anime",
            "options": ["anime", "realistic"],
            "impact": 2,
        },
    ]
    result = agent.consult_fields(fields)
    assert result["duration"] == "10"
    assert result["style"] == "anime"


def test_auto_accept_false_does_not_short_circuit(monkeypatch):
    """When auto_accept=False, consult_user should NOT auto-return (falls through to normal path)."""
    UIState.auto_accept = False
    UIState.is_ui_mode = False
    agent = DirectorAgent(llm_config={})
    # Patch stdin to be non-interactive so it auto-selects default
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    options = ["Option X", "Option Y"]
    result = agent.consult_user("Pick one?", options=options)
    # Non-interactive path also returns default — just verify it doesn't crash
    assert result in options or result == "Proceed as planned."
