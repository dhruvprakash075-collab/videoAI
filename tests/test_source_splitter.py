"""Tests for utils/source_splitter.py

Covers:
  - Pure helpers (_word_count, _split_sentences, _parse_md_headings,
    _rebalance, _parse_llm_chunks, _index)
  - _split_by_chapter (MD headings, DOCX metadata, fall-through to empty)
  - _split_by_word_count (Latin + Devanagari sentences, padding, error)
  - _split_by_llm (mocked Ollama; valid JSON, malformed, fallback)
  - split_source dispatcher (strategy selection, fallback, error paths)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from utils.source_loader import SourceDocument
from utils.source_splitter import (
    SegmentChunk,
    SourceSplitterError,
    _index,
    _parse_llm_chunks,
    _parse_md_headings,
    _rebalance,
    _split_by_chapter,
    _split_by_llm,
    _split_by_word_count,
    _split_sentences,
    _word_count,
    split_source,
)

# ── SourceDocument factory ──────────────────────────────────────────────────


def _doc(text="", source_type="txt", metadata=None, language="en"):
    return SourceDocument(
        text=text,
        word_count=_word_count(text),
        language=language,
        source_type=source_type,
        metadata=metadata or {},
    )


# ── Pure helpers ────────────────────────────────────────────────────────────


class TestWordCount:
    def test_empty(self):
        assert _word_count("") == 0

    def test_single(self):
        assert _word_count("hello") == 1

    def test_multiple(self):
        assert _word_count("the quick brown fox") == 4

    def test_devanagari(self):
        assert (
            _word_count("\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e")
            == 2
        )


class TestSplitSentences:
    def test_empty(self):
        assert _split_sentences("") == []

    def test_single(self):
        assert _split_sentences("Hello world.") == ["Hello world."]

    def test_latin(self):
        sents = _split_sentences("First sentence. Second sentence! Third?")
        assert sents == ["First sentence.", "Second sentence!", "Third?"]

    def test_devanagari_danda(self):
        text = "\u092a\u0939\u0932\u093e \u0935\u093e\u0915\u094d\u092f\u0964 \u0926\u0942\u0938\u0930\u093e \u0935\u093e\u0915\u094d\u092f\u0964"
        sents = _split_sentences(text)
        assert len(sents) == 2
        assert all("\u0964" in s for s in sents)

    def test_mixed_punctuation(self):
        sents = _split_sentences("End. \u0928\u092e\u0938\u094d\u0924\u0947\u0964 More?")
        assert len(sents) == 3

    def test_preserves_text(self):
        original = "Alpha. Beta. Gamma."
        sents = _split_sentences(original)
        assert " ".join(sents) == original


class TestParseMdHeadings:
    def test_no_headings(self):
        assert _parse_md_headings("Just some text.\nMore text.") == []

    def test_h1_only(self):
        md = "# Title\nBody text.\n# Chapter Two\nMore body."
        assert _parse_md_headings(md) == [(1, "Title"), (1, "Chapter Two")]

    def test_h2_only(self):
        md = "## Section A\nText.\n## Section B\nMore."
        assert _parse_md_headings(md) == [(2, "Section A"), (2, "Section B")]

    def test_mixed_h1_h2(self):
        md = "# Top\n## Sub\nBody.\n## Sub Two\nMore.\n# Top Two\nLast."
        result = _parse_md_headings(md)
        assert result == [(1, "Top"), (2, "Sub"), (2, "Sub Two"), (1, "Top Two")]

    def test_h3_ignored(self):
        md = "# A\n### ignored\nText."
        assert _parse_md_headings(md) == [(1, "A")]


class TestRebalance:
    def test_empty_returns_n(self):
        result = _rebalance([], 3)
        assert len(result) == 3
        assert all(c.text == "" for c in result)

    def test_exact_match(self):
        chunks = [SegmentChunk(text=f"chunk{i}") for i in range(3)]
        result = _rebalance(chunks, 3)
        assert len(result) == 3
        assert [c.text for c in result] == ["chunk0", "chunk1", "chunk2"]

    def test_merges_too_many(self):
        chunks = [SegmentChunk(text=f"word{i}") for i in range(10)]
        result = _rebalance(chunks, 2)
        assert len(result) == 2

    def test_splits_too_few(self):
        chunks = [SegmentChunk(text="alpha. beta. gamma. delta. epsilon. zeta.")]
        result = _rebalance(chunks, 3)
        assert len(result) == 3
        assert all(c.text for c in result)

    def test_indices_assigned(self):
        chunks = [SegmentChunk(text="a"), SegmentChunk(text="b")]
        result = _rebalance(chunks, 2)
        assert [c.index for c in result] == [0, 1]


class TestParseLlmChunks:
    def test_empty(self):
        assert _parse_llm_chunks("", 3) is None

    def test_valid_json(self):
        data = [{"text": f"t{i}"} for i in range(3)]
        result = _parse_llm_chunks(json.dumps(data), 3)
        assert result == data

    def test_more_than_expected_truncates(self):
        data = [{"text": f"t{i}"} for i in range(5)]
        result = _parse_llm_chunks(json.dumps(data), 3)
        assert result == data[:3]

    def test_bracket_depth_extraction(self):
        text = 'Here is the response: [{"text": "a"}, {"text": "b"}, {"text": "c"}]'
        result = _parse_llm_chunks(text, 3)
        assert result == [{"text": "a"}, {"text": "b"}, {"text": "c"}]

    def test_malformed_returns_none(self):
        assert _parse_llm_chunks("not json at all", 3) is None

    def test_too_few_items_returns_none(self):
        assert _parse_llm_chunks(json.dumps([{"text": "only one"}]), 3) is None


class TestIndex:
    def test_assigns_indices(self):
        chunks = [SegmentChunk(text="a"), SegmentChunk(text="b"), SegmentChunk(text="c")]
        _index(chunks)
        assert [c.index for c in chunks] == [0, 1, 2]


# ── _split_by_chapter ───────────────────────────────────────────────────────


class TestSplitByChapter:
    def test_md_three_h1(self):
        md = "# Chapter 1\nAlpha text here.\n# Chapter 2\nBeta text here.\n# Chapter 3\nGamma text here."
        source = _doc(text=md, source_type="md")
        chunks = _split_by_chapter(source, 3)
        assert len(chunks) == 3
        assert [c.source_chapter for c in chunks] == ["Chapter 1", "Chapter 2", "Chapter 3"]
        assert "Alpha" in chunks[0].text
        assert "Beta" in chunks[1].text
        assert "Gamma" in chunks[2].text

    def test_md_h2(self):
        md = "## A\nAlpha.\n## B\nBeta.\n## C\nGamma."
        source = _doc(text=md, source_type="md")
        chunks = _split_by_chapter(source, 3)
        assert len(chunks) == 3
        assert [c.source_chapter for c in chunks] == ["A", "B", "C"]

    def test_md_no_headings_returns_empty(self):
        source = _doc(text="Just plain text without structure.", source_type="md")
        assert _split_by_chapter(source, 3) == []

    def test_txt_returns_empty(self):
        source = _doc(text="Some text.", source_type="txt")
        assert _split_by_chapter(source, 3) == []

    def test_docx_uses_metadata(self):
        source = _doc(
            text="Body of A. Body of B. Body of C.",
            source_type="docx",
            metadata={"headings": [["Heading 1", "A"], ["Heading 1", "B"], ["Heading 1", "C"]]},
        )
        chunks = _split_by_chapter(source, 3)
        assert len(chunks) == 3
        assert chunks[0].source_chapter == "A"


# ── _split_by_word_count ────────────────────────────────────────────────────


class TestSplitByWordCount:
    def test_empty_text(self):
        assert _split_by_word_count("", 3, 100) == []

    def test_single_sentence(self):
        chunks = _split_by_word_count("Just one sentence here.", 3, 100)
        assert len(chunks) == 1

    def test_chunks_at_target(self):
        text = " ".join([f"Sentence number {i}." for i in range(20)])
        source = _doc(text=text, source_type="txt")
        chunks = split_source(
            source,
            4,
            {"source": {"split_strategy": "by_word_count"}, "script": {"words_per_segment": 10}},
        )
        assert len(chunks) == 4
        assert all(c.text for c in chunks)

    def test_devanagari_sentences(self):
        text = "\u092a\u0939\u0932\u093e \u0935\u093e\u0915\u094d\u092f\u0964 " * 20
        source = _doc(text=text, source_type="txt")
        chunks = split_source(
            source,
            4,
            {"source": {"split_strategy": "by_word_count"}, "script": {"words_per_segment": 10}},
        )
        assert len(chunks) == 4
        assert all(c.text for c in chunks)

    def test_pads_when_source_too_short(self):
        source = _doc(text="Short text.", source_type="txt")
        chunks = split_source(source, 5, {})
        assert len(chunks) == 5
        non_empty = [c for c in chunks if c.text]
        assert len(non_empty) == 1

    def test_preserves_text_verbatim(self):
        original = "First. Second. Third. Fourth. Fifth. Sixth."
        chunks = _split_by_word_count(original, 3, 5)
        combined = " ".join(c.text for c in chunks)
        assert combined == original


# ── _split_by_llm (mocked) ──────────────────────────────────────────────────


class TestSplitByLlm:
    def test_valid_response(self):
        source = _doc(text="Long source text " * 50, source_type="txt")
        llm_response = json.dumps(
            [
                {"text": "excerpt 1", "b_roll_hint": "city skyline", "key_event": "intro"},
                {"text": "excerpt 2", "b_roll_hint": "forest path", "key_event": "rising action"},
                {"text": "excerpt 3", "b_roll_hint": "climax", "key_event": "climax"},
            ]
        )
        config = {"models": {"writer": "zephyr-writer"}}
        with patch(
            "utils.crewai_breaker.guarded_ollama_call", return_value=llm_response
        ) as mock_call:
            chunks = _split_by_llm(source, 3, config)
        assert len(chunks) == 3
        assert chunks[0].text == "excerpt 1"
        assert chunks[0].b_roll_hint == "city skyline"
        assert chunks[0].key_event == "intro"
        assert mock_call.called
        _args, kwargs = mock_call.call_args
        assert kwargs["model"] == "zephyr-writer"
        assert kwargs["format_json"] is True

    def test_llm_returns_empty_signals_fallback(self):
        source = _doc(text="text " * 50, source_type="txt")
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            assert _split_by_llm(source, 3, {"models": {"writer": "w"}}) == []

    def test_llm_returns_malformed_signals_fallback(self):
        source = _doc(text="text " * 50, source_type="txt")
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value="not json"):
            assert _split_by_llm(source, 3, {"models": {"writer": "w"}}) == []

    def test_llm_returns_wrong_count_signals_fallback(self):
        source = _doc(text="text " * 50, source_type="txt")
        with patch(
            "utils.crewai_breaker.guarded_ollama_call",
            return_value=json.dumps([{"text": "a"}, {"text": "b"}]),
        ):
            assert _split_by_llm(source, 3, {"models": {"writer": "w"}}) == []

    def test_no_model_in_config_signals_fallback(self):
        source = _doc(text="text " * 50, source_type="txt")
        assert _split_by_llm(source, 3, {"models": {}}) == []

    def test_empty_source_returns_empty(self):
        source = _doc(text="", source_type="txt")
        assert _split_by_llm(source, 3, {"models": {"writer": "w"}}) == []


# ── split_source dispatcher ────────────────────────────────────────────────


class TestSplitSourceDispatcher:
    def test_default_strategy_is_by_word_count(self):
        text = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."
        source = _doc(text=text, source_type="txt")
        chunks = split_source(source, 3, {})
        assert len(chunks) == 3

    def test_by_chapter_md_uses_headings(self):
        md = "# A\nAlpha.\n# B\nBeta."
        source = _doc(text=md, source_type="md")
        chunks = split_source(source, 2, {"source": {"split_strategy": "by_chapter"}})
        assert [c.source_chapter for c in chunks] == ["A", "B"]

    def test_by_chapter_no_headings_falls_back(self):
        text = "Just plain prose without any structure. " * 20
        source = _doc(text=text, source_type="txt")
        chunks = split_source(source, 3, {"source": {"split_strategy": "by_chapter"}})
        assert len(chunks) == 3
        assert all(c.text for c in chunks)

    def test_by_word_count_explicit(self):
        text = " ".join([f"S{i}." for i in range(30)])
        source = _doc(text=text, source_type="txt")
        chunks = split_source(
            source,
            5,
            {"source": {"split_strategy": "by_word_count"}, "script": {"words_per_segment": 5}},
        )
        assert len(chunks) == 5

    def test_by_llm_uses_writer(self):
        llm_response = json.dumps(
            [{"text": f"t{i}", "b_roll_hint": f"b{i}", "key_event": f"e{i}"} for i in range(4)]
        )
        source = _doc(text="x " * 100, source_type="txt")
        config = {"source": {"split_strategy": "by_llm"}, "models": {"writer": "zephyr-writer"}}
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=llm_response):
            chunks = split_source(source, 4, config)
        assert len(chunks) == 4
        assert chunks[0].b_roll_hint == "b0"

    def test_by_llm_fails_falls_back_to_word_count(self):
        source = _doc(text="Sentence one. Sentence two. " * 10, source_type="txt")
        config = {"source": {"split_strategy": "by_llm"}, "models": {"writer": "w"}}
        with patch("utils.crewai_breaker.guarded_ollama_call", return_value=""):
            chunks = split_source(source, 3, config)
        assert len(chunks) == 3
        assert all(c.text for c in chunks)

    def test_invalid_strategy_raises(self):
        source = _doc(text="text", source_type="txt")
        with pytest.raises(SourceSplitterError, match="Unknown split_strategy"):
            split_source(source, 3, {"source": {"split_strategy": "by_magic"}})

    def test_n_segments_zero_raises(self):
        source = _doc(text="text", source_type="txt")
        with pytest.raises(SourceSplitterError, match="n_segments must be > 0"):
            split_source(source, 0, {})

    def test_n_segments_negative_raises(self):
        source = _doc(text="text", source_type="txt")
        with pytest.raises(SourceSplitterError):
            split_source(source, -1, {})

    def test_empty_source_returns_n_empty_chunks(self):
        source = _doc(text="", source_type="txt")
        chunks = split_source(source, 4, {})
        assert len(chunks) == 4
        assert all(c.text == "" for c in chunks)

    def test_whitespace_only_source(self):
        source = _doc(text="   \n\n   ", source_type="txt")
        chunks = split_source(source, 3, {})
        assert len(chunks) == 3
        assert all(c.text == "" for c in chunks)

    def test_indices_always_assigned(self):
        source = _doc(text="One. Two. Three. Four. Five. Six.", source_type="txt")
        chunks = split_source(source, 3, {})
        assert [c.index for c in chunks] == [0, 1, 2]


# ── End-to-end (source_loader + splitter) ──────────────────────────────────


class TestEndToEnd:
    def test_txt_source_pipes_through_splitter(self):
        from utils.source_loader import load_source

        body = "Sentence one. Sentence two. " * 20
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            tmp_path = Path(f.name)
        try:
            doc = load_source(tmp_path, {})
            chunks = split_source(
                doc,
                4,
                {
                    "source": {"split_strategy": "by_word_count"},
                    "script": {"words_per_segment": 20},
                },
            )
            assert len(chunks) == 4
            assert all(c.text for c in chunks)
        finally:
            tmp_path.unlink()
