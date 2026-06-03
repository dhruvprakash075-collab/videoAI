"""test_director_agent_extended.py - Extended unit tests for agents/director_agent.py"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.director_agent import DirectorAgent


@pytest.fixture
def agent():
    """A DirectorAgent instance with custom mock configs."""
    return DirectorAgent(
        llm_config={
            "models": {
                "director": "director-model",
                "translator": "translator-model",
                "writer": "writer-model",
            },
            "ollama": {"host": "http://localhost:11434"},
            "cache_dir": "/tmp/extended_director_cache",
            "tts": {
                "devanagari": {
                    "max_latin_ratio": 0.10,
                    "max_retranslate_retries": 2,
                }
            },
        },
        memory=None,
    )


def test_director_agent_resolve_model(agent):
    assert agent._resolve_model("director") == "director-model"
    assert agent._resolve_model("translator") == "translator-model"
    assert agent._resolve_model("unknown") == "llama3"


def test_director_agent_prewarm_ollama(agent):
    with patch("urllib.request.urlopen") as mock_urlopen, patch("threading.Thread") as mock_thread:
        # Mock Thread to execute synchronously
        def mock_thread_init(target, args=(), kwargs=None, daemon=True):
            if kwargs is None:
                kwargs = {}
            mock_t = MagicMock()
            mock_t.start = lambda: target(*args, **kwargs)
            return mock_t

        mock_thread.side_effect = mock_thread_init

        agent._prewarm_ollama()
        mock_urlopen.assert_called()


def test_director_agent_call_ollama_streaming(agent):
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.__iter__.return_value = [
            b'{"response": "streamed output"}\n',
            b'{"response": "", "done": true, "total_duration": 1000000000}\n',
        ]
        mock_urlopen.return_value.__enter__.return_value = mock_res

        res = agent._call_ollama_streaming("prompt", "test-label")
        assert "streamed output" in res


def test_director_consult_on_config(agent):
    vision_doc = {
        "characters": [
            {"name": "Alice", "description": "A smart detective"},
            {"name": "Bob", "description": "The assistant"},
        ],
        "theme": "A Mysterious Murder",
        "visual_style": "Noir",
        "pacing": "moderate",
        "emotions": "tension",
        "ambiguity_detected": True,
        "ambiguity_question": "Who is the killer?",
        "ambiguity_fields": ["visual_style", "pacing"],
    }

    # First we mock consult_user for ambiguity resolution, then options questionnaire, then custom instructions
    questionnaire_resp = json.dumps(
        {
            "fields": {
                "visual_style": {"options": ["Cyberpunk", "Classic Noir"]},
                "pacing": {"options": ["Slow-burn", "Fast-paced"]},
            },
            "breakdown": {
                "segment_count": 4,
                "words_per_segment": 150,
                "image_count_per_segment": 8,
                "opening_hook_style": "in media res",
                "pacing_notes": "build up tension slowly",
            },
        }
    )

    with (
        patch.object(
            agent,
            "consult_user",
            side_effect=["Resolved", "Cyberpunk", "No additional instructions"],
        ),
        patch.object(
            agent,
            "_call_ollama",
            side_effect=[questionnaire_resp, '{"options": ["Tweak 1", "Tweak 2"]}'],
        ),
        patch.object(
            agent,
            "consult_fields",
            return_value={"visual_style": "Cyberpunk", "pacing": "Slow-burn"},
        ),
    ):
        user_resp, writer_input = agent.consult_on_config(vision_doc)

        assert user_resp["ambiguity_resolution"] == "Resolved"
        assert user_resp["visual_style"] == "Cyberpunk"
        assert writer_input["segment_count"] == 4


def test_director_consult_on_config_default_fallback(agent):
    vision_doc = {
        "characters": "OnlyOneCharacter",
        "theme": "OnlyOneTheme",
        "ambiguity_detected": False,
        "ambiguity_fields": ["tts_engine"],
    }
    # No ambiguity q, tts_engine field. Questionnaire returns malformed options
    questionnaire_resp = "invalid json {..."
    with (
        patch.object(agent, "consult_user", return_value="Proceed as planned."),
        patch.object(agent, "_call_ollama", side_effect=[questionnaire_resp, ""]),
        patch.object(agent, "consult_fields", return_value={"tts_engine": "Keep as-is: omnivoice"}),
    ):
        user_resp, _writer_input = agent.consult_on_config(vision_doc)
        assert "tts_engine" not in user_resp  # identical to default, skipped


def test_director_consult_with_writer(agent):
    vision_doc = {
        "theme": "Epic Battle",
        "visual_style": "anime",
        "pacing": "fast",
        "emotions": "excitement",
        "recommendations": ["Make action fast"],
    }

    writer_resp = json.dumps(
        {
            "segment_count": 5,
            "words_per_segment": 120,
            "image_count_per_segment": 7,
            "opening_hook_style": "explosion",
            "pacing_notes": "very fast",
        }
    )

    with patch.object(agent, "_call_ollama", return_value=writer_resp):
        res = agent.consult_with_writer(vision_doc, {"visual_style": "anime"})
        assert res["segment_count"] == 5
        assert res["words_per_segment"] == 120


def test_director_consult_with_writer_fallback(agent):
    vision_doc = {
        "theme": "Epic Battle",
        "visual_style": "anime",
    }

    # Temporarily empty prompt templates to force fallback path (line 1273)
    original_prompts = DirectorAgent._prompts
    DirectorAgent._prompts = {}

    writer_resp = json.dumps(
        {
            "segment_count": 3,
            "words_per_segment": 130,
            "image_count_per_segment": 6,
        }
    )

    try:
        with patch.object(agent, "_call_ollama", return_value=writer_resp):
            res = agent.consult_with_writer(vision_doc, {})
            assert res["segment_count"] == 3
    finally:
        DirectorAgent._prompts = original_prompts


def test_director_analyze_with_research(agent, tmp_path):
    agent.llm_config["cache_dir"] = str(tmp_path)
    research = {"combined_summary": "Interesting research summary"}

    llm_resp = json.dumps(
        {
            "characters": [{"name": "Protagonist", "description": "Hero"}],
            "visual_style": "Watercolor",
            "theme": "Topic Theme",
            "shot_distribution": {"establishing": 0.2, "environment": 0.8},
            "tts_recommendation": "edge",
            "recommended_duration_min": 15,
        }
    )

    with patch.object(agent, "_call_ollama", return_value=llm_resp):
        vision = agent.analyze_with_research("Topic Theme", research)
        assert vision["visual_style"] == "Watercolor"
        assert vision["recommended_duration_min"] == 15
        assert vision["tts_recommendation"] == "edge"


def test_director_produce_runtime_config(agent):
    vision_doc = {
        "theme": "My Theme",
        "visual_style": "anime",
        "pacing": "moderate",
        "emotions": "happy",
        "characters": {
            "Hero": {"description": "A brave hero"},
            "Villain": "An evil guy",
        },
    }

    user_responses = {
        "visual_style": "watercolor",
        "subtitle_style": "classic",
        "tts_engine": "edge",
        "custom_instructions": "Make it happy",
    }

    writer_input = {
        "segment_count": 4,
        "words_per_segment": 140,
        "image_count_per_segment": 8,
        "opening_hook_style": "smile",
        "pacing_notes": "happy beats",
    }

    res = agent.produce_runtime_config(vision_doc, user_responses, writer_input, mode="full")
    assert "watercolor" in res["visual"]["style"]
    assert res["tts"]["engine"] == "edge"
    assert "hero" in res["characters"]
    assert "villain" in res["characters"]

    # Check invalid vision_doc/responses/writer input fallback types
    res_fallback = agent.produce_runtime_config(None, None, None, mode="full")
    assert (
        "anime" in res_fallback["visual"]["style"]
        or "hybrid 2d anime" in res_fallback["visual"]["style"]
    )


def test_director_agent_consult_user_interactive(agent):
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["1"]):
        res = agent.consult_user("Pick one?", options=["Option A", "Option B"])
        assert res == "Option A"


def test_director_agent_consult_user_interactive_custom(agent):
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["0", "My Custom Value"]),
    ):
        res = agent.consult_user("Pick one?", options=["Option A", "Option B"], allow_custom=True)
        assert res == "My Custom Value"


def test_director_agent_consult_user_interactive_invalid_then_valid(agent):
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["99", "abc", "2"]),
    ):
        res = agent.consult_user("Pick one?", options=["Option A", "Option B"])
        assert res == "Option B"


def test_director_agent_consult_user_interactive_no_options(agent):
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["Hello There"]),
    ):
        res = agent.consult_user("Input something?", options=[])
        assert res == "Hello There"


def test_director_consult_on_duration_keep(agent):
    with patch.object(agent, "consult_user", return_value="Keep estimated duration"):
        res = agent.consult_on_duration(10)
        assert res["action"] == "keep"
        assert res["target_minutes"] == 10


def test_director_consult_on_duration_adjust(agent):
    with patch.object(agent, "consult_user", side_effect=["Reduce or adjust", "8"]):
        res = agent.consult_on_duration(10)
        assert res["action"] == "adjusted"
        assert res["target_minutes"] == 8


def test_director_consult_on_duration_invalid(agent):
    with patch.object(agent, "consult_user", side_effect=["Reduce or adjust", "invalid-number"]):
        res = agent.consult_on_duration(10)
        assert res["action"] == "keep"
        assert res["target_minutes"] == 10


def test_director_suggest_cliffhangers(agent):
    llm_resp = json.dumps(
        {
            "cliffhangers": [
                {"point": 45, "outcome": "Cliffhanger A", "reason": "Reason A"},
                {"point": 80, "outcome": "Cliffhanger B", "reason": "Reason B"},
            ]
        }
    )
    # Long text (> 200 characters) to bypass fallback condition
    long_content = (
        "This is a long story content word padding to ensure that the content is at least two hundred characters long so that it doesn't trigger the fallback path in suggest_cliffhangers. "
        * 3
    )
    with patch.object(agent, "_call_ollama", return_value=llm_resp):
        res = agent.suggest_cliffhangers(long_content, 3.0)
        assert len(res) == 2
        assert res[0]["point"] == 45
        assert res[1]["outcome"] == "Cliffhanger B"


def test_director_suggest_cliffhangers_fallback(agent):
    long_content = (
        "This is a long story content word padding to ensure that the content is at least two hundred characters long so that it doesn't trigger the fallback path in suggest_cliffhangers. "
        * 3
    )
    with patch.object(agent, "_call_ollama", side_effect=Exception("LLM error")):
        res = agent.suggest_cliffhangers(long_content, 3.0)
        assert len(res) == 2
        assert res[0]["point"] == 50


def test_director_translate_to_devanagari_characters_and_retries(agent):
    agent.llm_config = {
        "characters": {
            "hero": {"name": "HeroName", "description": "Desc of Hero"},
            "villain": {"name": "VillainName"},
        },
        "tts": {"devanagari": {"max_latin_ratio": 0.05, "max_retranslate_retries": 2}},
    }
    # Initial translation must have >= 10 Devanagari chars but low ratio (contains english)
    first_attempt = "पहला हिंदी अनुवाद with too many english/latin letters to trigger retry"
    with patch.object(agent, "_call_ollama_chat", side_effect=[first_attempt, "सटीक हिंदी अनुवाद"]):
        res = agent.translate_to_devanagari(
            "English text", {"mood": "dark", "key_event": "event"}, "context"
        )
        assert "सटीक हिंदी अनुवाद" in res


def test_director_translate_to_devanagari_retry_failure(agent):
    agent.llm_config = None  # test llm_config fallback path
    # Initial translation has Devanagari chars but low ratio. Stricter retry raises exception/fails.
    first_attempt = "पहला हिंदी अनुवाद with too many english/latin letters to trigger retry"
    with patch.object(
        agent, "_call_ollama_chat", side_effect=[first_attempt, Exception("Ollama disconnected")]
    ):
        res = agent.translate_to_devanagari("English text", {}, "")
        # Returns best (which is the first attempt)
        assert "पहला हिंदी अनुवाद" in res


def test_director_generate_hinglish_script(agent):
    with patch.object(
        agent, "_call_ollama", return_value="[narration] Hinglish narration here [/narration]"
    ):
        res = agent.generate_hinglish_script(
            {"summary": "summarized event", "key_event": "climax", "mood": "action"}
        )
        assert res == "Hinglish narration here"

    with patch.object(agent, "_call_ollama", side_effect=Exception("Ollama fail")):
        res = agent.generate_hinglish_script(
            {"summary": "summarized event", "key_event": "climax", "mood": "action"}
        )
        assert "summarized event" in res


def test_director_parse_json_custom_extraction(agent):
    # Test valid JSON within braces but with preceding invalid block
    text = 'pre { invalid json } text { "valid": 42 } post'
    res = agent._parse_json(text, fallback={"default": 1})
    assert res == {"valid": 42}

    # Test completely malformed JSON triggers fallback
    text_bad = "no json braces at all"
    res_bad = agent._parse_json(text_bad, fallback={"default": 99})
    assert res_bad == {"default": 99}


def test_director_consult_user_stdin_exceptions(agent):
    # Test sys.stdin.isatty raising exception
    with patch("sys.stdin.isatty", side_effect=Exception("tty error")):
        res = agent.consult_user("Question?", options=["Opt A"])
        assert res == "Opt A"

    # Test KeyboardInterrupt inside input
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=KeyboardInterrupt),
    ):
        res = agent.consult_user("Question?", options=["Opt A"])
        assert res == "Opt A"

    # Test OSError inside input (e.g. background process Windows)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=OSError("Errno 22")),
    ):
        res = agent.consult_user("Question?", options=["Opt A"])
        assert res == "Opt A"


def test_director_consult_user_paginated_and_custom(agent):
    # 1. Paginated options > 12, select custom input
    options = [f"Option {i}" for i in range(1, 15)]
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["0", "My Custom Value"]),
    ):
        res = agent.consult_user("Question?", options=options, allow_custom=True)
        assert res == "My Custom Value"

    # 2. Too many invalid attempts (> 50 attempts)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["99"] * 55),
    ):
        res = agent.consult_user("Question?", options=options)
        assert res == "Option 1"


def test_director_consult_user_empty_options(agent):
    # Test empty options, returning None (EOF)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=[KeyboardInterrupt]),
    ):
        res = agent.consult_user("Question?", options=[])
        assert res == "Proceed with default settings."

    # Test empty options, returning empty string
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=[""]),
    ):
        res = agent.consult_user("Question?", options=[])
        assert res == "Proceed as planned."

    # Test empty options, returning valid string
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=["Custom Response"]),
    ):
        res = agent.consult_user("Question?", options=[])
        assert res == "Custom Response"


def test_director_characters_list_handling(agent):
    # characters as dict containing both a dict details and a string details
    vision_doc_dict = {"characters": {"Alice": {"description": "Detective"}, "Bob": "Assistant"}}
    # characters as string
    vision_doc_str = {"characters": "Alice"}
    # characters as list of strings
    vision_doc_list_str = {"characters": ["Alice", "Bob"]}

    # Verify consult_on_config runs with these structures without crashing
    with (
        patch.object(agent, "consult_user", return_value="Proceed as planned."),
        patch.object(agent, "_call_ollama", return_value="{}"),
    ):
        agent.consult_on_config(vision_doc_dict)
        agent.consult_on_config(vision_doc_str)
        agent.consult_on_config(vision_doc_list_str)


def test_director_consult_on_config_option_relevance(agent):
    vision_doc = {
        "pacing": "slow",
        "ambiguity_detected": False,
        "ambiguity_fields": ["pacing"],
    }
    # Questionnaire returns options that have no overlap with "slow" (e.g. "fast")
    questionnaire_resp = json.dumps(
        {"fields": {"pacing": {"options": ["fast-paced", "energetic"]}}, "breakdown": {}}
    )

    with (
        patch.object(agent, "consult_user", return_value="Keep as-is: slow"),
        patch.object(agent, "_call_ollama", side_effect=[questionnaire_resp, ""]),
        patch.object(
            agent, "consult_fields", return_value={"pacing": "Keep as-is: slow"}
        ) as mock_consult_fields,
    ):
        agent.consult_on_config(vision_doc)
        # Verify options passed to consult_fields contains prepended "Keep as-is: slow"
        called_fields = mock_consult_fields.call_args[0][0]
        pacing_field = next(f for f in called_fields if f["key"] == "pacing")
        assert pacing_field["options"][0] == "Keep as-is: slow"
