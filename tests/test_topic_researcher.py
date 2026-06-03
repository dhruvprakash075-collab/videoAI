"""test_topic_researcher.py - brainstorm_topic fallback + happy path."""

from unittest.mock import patch


def test_brainstorm_returns_topic_on_success():
    from utils.topic_researcher import brainstorm_topic

    cfg = {"models": {"director": "test-director"}}
    with patch("utils.topic_researcher.guarded_ollama_call", return_value='"My Test Topic"'):
        out = brainstorm_topic(cfg)
    assert out == "My Test Topic"


def test_brainstorm_strips_quotes_and_whitespace():
    from utils.topic_researcher import brainstorm_topic

    cfg = {"models": {"director": "x"}}
    with patch("utils.topic_researcher.guarded_ollama_call", return_value="  'Quoted Topic'  "):
        out = brainstorm_topic(cfg)
    assert out == "Quoted Topic"


def test_brainstorm_falls_back_on_exception():
    from utils.topic_researcher import brainstorm_topic

    cfg = {"models": {"director": "x"}}
    with patch("utils.topic_researcher.guarded_ollama_call", side_effect=RuntimeError("boom")):
        out = brainstorm_topic(cfg)
    assert out == "The Mysteries of the Deep Ocean"


def test_brainstorm_falls_back_on_empty():
    from utils.topic_researcher import brainstorm_topic

    cfg = {"models": {"director": "x"}}
    with patch("utils.topic_researcher.guarded_ollama_call", return_value=""):
        out = brainstorm_topic(cfg)
    assert out == "The Mysteries of the Deep Ocean"


def test_brainstorm_uses_default_model_when_none_in_config():
    from utils.topic_researcher import brainstorm_topic

    with patch("utils.topic_researcher.guarded_ollama_call", return_value="X") as m:
        brainstorm_topic({})
    assert m.called
    _, kwargs = m.call_args
    assert kwargs["model"] == "hermes-director"


def test_brainstorm_uses_load_config_when_none_passed():
    from utils.topic_researcher import brainstorm_topic

    with (
        patch(
            "utils.topic_researcher.load_config",
            return_value={"models": {"director": "loaded-model"}},
        ) as lc,
        patch("utils.topic_researcher.guarded_ollama_call", return_value="Loaded") as goc,
    ):
        out = brainstorm_topic()
    assert lc.called
    assert out == "Loaded"
    _, kwargs = goc.call_args
    assert kwargs["model"] == "loaded-model"
