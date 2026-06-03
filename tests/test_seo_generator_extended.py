"""Tests for utils/seo_generator.py (Phase 5 extension)

Covers:
  - SEOMetadata TypedDict: all keys optional, backward-compat dict access
  - _slugify_tag: lowercase, strips punctuation, handles Unicode
  - _chapter_timecode: 0:00, single-digit minutes, 1:00:00+ hours
  - _build_chapters: from outline, empty outline, cap
  - _extract_tag_candidates: from topic, outline, research; dedup; stopwords
  - _derive_hashtags: top-N with # prefix
  - _fallback_metadata: never raises, all fields present, source_path flag
  - _parse_seo_response: direct, bracket depth, malformed
  - generate_seo_metadata (mocked LLM): valid, breaker open, malformed, disabled,
    empty outline, source path, research enrichment, backward compat (title+tags)
"""

from __future__ import annotations

import json
from unittest.mock import patch

from utils.seo_generator import (
    SEOMetadata,
    _build_chapters,
    _build_prompt,
    _chapter_timecode,
    _derive_hashtags,
    _extract_tag_candidates,
    _fallback_metadata,
    _parse_seo_response,
    _slugify_tag,
    generate_seo_metadata,
)

# ── SEOMetadata TypedDict ───────────────────────────────────────────────────


class TestSEOMetadata:
    def test_all_keys_optional(self):
        m: SEOMetadata = {}
        assert m.get("title") is None
        assert m.get("tags") is None

    def test_dict_access_backward_compat(self):
        m: SEOMetadata = {"title": "T", "tags": ["a", "b"]}
        assert m["title"] == "T"
        assert m["tags"] == ["a", "b"]


# ── _slugify_tag ────────────────────────────────────────────────────────────


class TestSlugify:
    def test_lowercase(self):
        assert _slugify_tag("Hello") == "hello"

    def test_strips_punctuation(self):
        assert _slugify_tag("Hello, World!") == "helloworld"

    def test_preserves_hyphens(self):
        assert _slugify_tag("long-form") == "long-form"

    def test_devanagari(self):
        assert (
            _slugify_tag("\u0905\u092e\u0947\u091c\u094b\u0928")
            == "\u0905\u092e\u0947\u091c\u094b\u0928"
        )

    def test_empty(self):
        assert _slugify_tag("") == ""
        assert _slugify_tag("   ") == ""

    def test_max_len(self):
        assert _slugify_tag("a" * 50, max_len=10) == "a" * 10


# ── _chapter_timecode ───────────────────────────────────────────────────────


class TestChapterTimecode:
    def test_zero(self):
        assert _chapter_timecode(0, 5) == "0:00"

    def test_total_zero(self):
        assert _chapter_timecode(0, 0) == "0:00"

    def test_mid(self):
        assert _chapter_timecode(2, 5) == "2:00"

    def test_with_hours(self):
        assert _chapter_timecode(1, 3) == "1:00"


# ── _build_chapters ─────────────────────────────────────────────────────────


class TestBuildChapters:
    def test_empty(self):
        assert _build_chapters([], 10) == []

    def test_from_key_events(self):
        outline = [{"key_event": "Intro"}, {"key_event": "Body"}, {"key_event": "End"}]
        chs = _build_chapters(outline, 10)
        assert len(chs) == 3
        assert chs[0]["title"] == "Intro"
        assert chs[0]["time"] == "0:00"

    def test_falls_back_to_summary(self):
        outline = [{"summary": "The start"}]
        assert _build_chapters(outline, 10)[0]["title"] == "The start"

    def test_falls_back_to_title(self):
        outline = [{"title": "The Title"}]
        assert _build_chapters(outline, 10)[0]["title"] == "The Title"

    def test_falls_back_to_part_n(self):
        outline = [{}]
        assert _build_chapters(outline, 10)[0]["title"] == "Part 1"

    def test_cap(self):
        outline = [{"key_event": f"e{i}"} for i in range(20)]
        chs = _build_chapters(outline, 5)
        assert len(chs) == 5


# ── _extract_tag_candidates ─────────────────────────────────────────────────


class TestExtractTagCandidates:
    def test_topic_words(self):
        tags = _extract_tag_candidates("The Amazon River", [])
        assert "amazon" in tags
        assert "river" in tags
        assert "the" not in tags

    def test_outline_events(self):
        outline = [{"key_event": "Discovery of the ruins"}, {"key_event": "Ancient mystery"}]
        tags = _extract_tag_candidates("", outline)
        assert "discovery" in tags
        assert "ruins" in tags
        assert "ancient" in tags
        assert "mystery" in tags

    def test_research_titles(self):
        tags = _extract_tag_candidates("", [], ["Amazon Expedition", "Forest Discovery"])
        assert "amazon" in tags
        assert "expedition" in tags
        assert "forest" in tags
        assert "discovery" in tags

    def test_dedup(self):
        tags = _extract_tag_candidates("Amazon Amazon Amazon", [])
        assert tags.count("amazon") == 1

    def test_stopwords_filtered(self):
        tags = _extract_tag_candidates("The and or but not", [])
        assert "the" not in tags
        assert "and" not in tags

    def test_short_words_filtered(self):
        tags = _extract_tag_candidates("a be I ran to home", [])
        assert "ran" in tags
        assert "home" in tags
        assert "to" not in tags
        assert "be" not in tags


# ── _derive_hashtags ────────────────────────────────────────────────────────


class TestDeriveHashtags:
    def test_prefix_with_hash(self):
        assert _derive_hashtags(["amazon", "river"], 2) == ["#amazon", "#river"]

    def test_caps_at_count(self):
        assert _derive_hashtags(["a", "b", "c", "d"], 2) == ["#a", "#b"]

    def test_empty_input(self):
        assert _derive_hashtags([], 5) == []


# ── _fallback_metadata ──────────────────────────────────────────────────────


class TestFallbackMetadata:
    def test_all_fields_present(self):
        meta = _fallback_metadata(
            "Test Topic", [{"key_event": "Part 1"}], {}, language="en", source_path=False
        )
        for key in (
            "title",
            "description",
            "tags",
            "hashtags",
            "chapters",
            "language",
            "source_path",
            "generation_succeeded",
        ):
            assert key in meta

    def test_title_is_topic(self):
        meta = _fallback_metadata("My Topic", [], {}, "en", False)
        assert meta["title"] == "My Topic"

    def test_source_path_flag(self):
        meta = _fallback_metadata("t", [], {}, "hi", True)
        assert meta["source_path"] is True
        assert meta["language"] == "hi"

    def test_generation_succeeded_false(self):
        meta = _fallback_metadata("t", [], {}, "en", False)
        assert meta["generation_succeeded"] is False

    def test_description_contains_chapters(self):
        outline = [{"key_event": "A"}, {"key_event": "B"}]
        meta = _fallback_metadata("t", outline, {}, "en", False)
        assert "Chapters:" in meta["description"]
        assert "A" in meta["description"]
        assert "B" in meta["description"]

    def test_title_max_chars_respected(self):
        long_topic = "x" * 200
        meta = _fallback_metadata(long_topic, [], {"seo": {"title_max_chars": 50}}, "en", False)
        assert len(meta["title"]) == 50


# ── _parse_seo_response ─────────────────────────────────────────────────────


class TestParseSeoResponse:
    def test_empty(self):
        assert _parse_seo_response("") is None
        assert _parse_seo_response("   ") is None

    def test_valid_direct(self):
        raw = json.dumps({"title": "t", "description": "d", "tags": ["a"]})
        assert _parse_seo_response(raw) == {"title": "t", "description": "d", "tags": ["a"]}

    def test_bracket_depth(self):
        raw = 'Here: {"title": "t", "tags": []} and more'
        parsed = _parse_seo_response(raw)
        assert parsed is not None
        assert parsed["title"] == "t"

    def test_malformed(self):
        assert _parse_seo_response("not json") is None

    def test_array_returns_none(self):
        assert _parse_seo_response(json.dumps([1, 2, 3])) is None


# ── _build_prompt ───────────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_contains_topic(self):
        prompt = _build_prompt("Amazon", [{"key_event": "e"}], {}, "en", False, None)
        assert "Amazon" in prompt
        assert "e" in prompt

    def test_source_path_block(self):
        prompt = _build_prompt("t", [], {}, "hi", True, None)
        assert "hi" in prompt
        assert "source document" in prompt.lower()

    def test_research_block(self):
        prompt = _build_prompt("t", [], {}, "en", False, ["Breaking News", "Big Story"])
        assert "Breaking News" in prompt
        assert "Big Story" in prompt

    def test_no_research_no_block(self):
        prompt = _build_prompt("t", [], {}, "en", False, None)
        assert "Related research" not in prompt

    def test_title_max_in_prompt(self):
        prompt = _build_prompt("t", [], {"seo": {"title_max_chars": 80}}, "en", False, None)
        assert "80" in prompt

    def test_tag_count_in_prompt(self):
        prompt = _build_prompt("t", [], {"seo": {"tags_count": 20}}, "en", False, None)
        assert "20" in prompt


# ── generate_seo_metadata (mocked LLM) ──────────────────────────────────────


class TestGenerateSeoMetadata:
    def test_valid_llm_response(self):
        llm = json.dumps(
            {
                "title": "Amazing Amazon Secrets",
                "description": "Discover the hidden wonders...",
                "tags": ["amazon", "river", "mystery", "documentary", "nature"],
            }
        )
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm) as mock_call:
            meta = generate_seo_metadata(
                "Amazon",
                [{"key_event": "e1"}, {"key_event": "e2"}],
                {"models": {"director": "hermes"}},
            )
        assert meta["title"] == "Amazing Amazon Secrets"
        assert meta["generation_succeeded"] is True
        assert "amazon" in meta["tags"]
        assert meta["language"] == "en"
        assert meta["source_path"] is False
        assert len(meta["chapters"]) == 2
        assert mock_call.called
        _args, kwargs = mock_call.call_args
        assert kwargs["model"] == "hermes"
        assert kwargs["format_json"] is True

    def test_breaker_open_falls_back(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            meta = generate_seo_metadata("Amazon", [{"key_event": "e1"}], {})
        assert meta["generation_succeeded"] is False
        assert meta["title"] == "Amazon"
        assert meta["source_path"] is False

    def test_malformed_falls_back(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value="not json"):
            meta = generate_seo_metadata("Amazon", [], {})
        assert meta["generation_succeeded"] is False

    def test_disabled_uses_fallback(self):
        meta = generate_seo_metadata(
            "Amazon", [], {"seo": {"enabled": False}, "models": {"director": "hermes"}}
        )
        assert meta["generation_succeeded"] is False
        assert meta["title"] == "Amazon"

    def test_empty_outline_no_research_falls_back(self):
        meta = generate_seo_metadata("Amazon", [], {})
        assert meta["generation_succeeded"] is False

    def test_source_path_aware(self):
        llm = json.dumps({"title": "T", "description": "D", "tags": ["x"]})
        source = type("Doc", (), {"language": "hi"})()
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata("Amazon", [], {}, source_document=source)
        assert meta["source_path"] is True
        assert meta["language"] == "hi"

    def test_research_items_enrich(self):
        llm = json.dumps({"title": "T", "description": "D", "tags": ["x"]})
        research = [type("R", (), {"title": "Breaking News"})()]
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm) as mock_call:
            meta = generate_seo_metadata("Amazon", [], {}, research_items=research)
        prompt = mock_call.call_args.args[0]
        assert "Breaking News" in prompt
        assert meta["generation_succeeded"] is True

    def test_llm_returns_no_tags_fills_from_outline(self):
        llm = json.dumps({"title": "T", "description": "D", "tags": []})
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata("Amazon", [{"key_event": "river discovery"}], {})
        assert meta["tags"]
        assert "river" in meta["tags"] or "discovery" in meta["tags"]

    def test_backward_compat_title_and_tags(self):
        llm = json.dumps({"title": "Old Style Title", "description": "d", "tags": ["a", "b"]})
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata("Amazon", [{"key_event": "e1"}], {})
        assert meta["title"] == "Old Style Title"
        assert "a" in meta["tags"]
        assert "b" in meta["tags"]

    def test_hashtags_present(self):
        llm = json.dumps(
            {
                "title": "T",
                "description": "D",
                "tags": ["amazon", "river", "mystery", "nature", "doc"],
            }
        )
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata("Amazon", [], {})
        assert all(h.startswith("#") for h in meta["hashtags"])

    def test_title_max_chars_enforced(self):
        llm = json.dumps({"title": "x" * 200, "description": "d", "tags": ["a"]})
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata(
                "Amazon", [{"key_event": "e1"}], {"seo": {"title_max_chars": 50}}
            )
        assert len(meta["title"]) == 50

    def test_chapters_built_from_outline(self):
        llm = json.dumps({"title": "T", "description": "D", "tags": ["a"]})
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm):
            meta = generate_seo_metadata("Amazon", [{"key_event": f"e{i}"} for i in range(5)], {})
        assert len(meta["chapters"]) == 5
        assert meta["chapters"][0]["time"] == "0:00"
        assert meta["chapters"][-1]["time"] != "0:00"

    def test_never_raises(self):
        with patch("utils.crewai_breaker.guarded_ollama_call", side_effect=Exception("boom")):
            meta = generate_seo_metadata("Amazon", [], {})
        assert meta["generation_succeeded"] is False
        assert meta["title"] == "Amazon"


# ── End-to-end smoke ────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_source_path_full_flow(self):
        llm = json.dumps(
            {
                "title": "The Amazon: Hidden World",
                "description": "An exploration of the Amazon river...",
                "tags": ["amazon", "river", "nature", "exploration", "documentary"],
            }
        )
        source = type("Doc", (), {"language": "hi", "metadata": {"title": "Amazon Book"}})()
        research = [
            type("R", (), {"title": "Amazon Expedition 2024"})(),
            type("R", (), {"title": "Deforestation Crisis"})(),
        ]
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm) as mock_call:
            meta = generate_seo_metadata(
                "The Amazon",
                [{"key_event": "Introduction"}, {"key_event": "The journey begins"}],
                {"models": {"director": "hermes"}},
                source_document=source,
                research_items=research,
            )
        assert meta["title"] == "The Amazon: Hidden World"
        assert meta["source_path"] is True
        assert meta["language"] == "hi"
        assert len(meta["chapters"]) == 2
        assert len(meta["hashtags"]) > 0
        prompt = mock_call.call_args.args[0]
        assert "Amazon Expedition 2024" in prompt
        assert "Deforestation Crisis" in prompt
        assert "hi" in prompt
