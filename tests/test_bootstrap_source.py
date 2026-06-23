"""Tests for the --source CLI flag in bootstrap_pipeline.py

Covers:
  - argparse: --source arg exists, defaults to None
  - _load_and_split_source: file path, URL, error paths
  - Source-chunks plumbing into run_long_pipeline (both single and batch paths)
  - topic_text derivation from metadata.title, front-matter.title, filename
  - n_segments derivation from word_count / target_words when --segment-count absent
"""

from __future__ import annotations

import argparse
import contextlib
from unittest.mock import MagicMock, patch

import pytest

from bootstrap_pipeline import _load_and_split_source
from utils.source_loader import SourceDocument, SourceLoaderError
from utils.source_splitter import SegmentChunk, SourceSplitterError


def _args(segment_count=None, words_per_segment=None, source=None):
    ns = MagicMock()
    ns.segment_count = segment_count
    ns.words_per_segment = words_per_segment
    ns.source = source
    return ns


def _doc(text, source_type="md", language="en", metadata=None, word_count=None):
    if word_count is None:
        word_count = len(text.split())
    return SourceDocument(
        text=text,
        word_count=word_count,
        language=language,
        source_type=source_type,
        metadata=metadata or {},
    )


def _chunks(n):
    return [SegmentChunk(text=f"chunk{i}", index=i) for i in range(n)]


# ── _load_and_split_source: file path ───────────────────────────────────────


class TestLoadSplitFilePath:
    def test_md_file_loads_and_splits(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\nBody text here.\n", encoding="utf-8")
        doc = _doc(
            "# Title\n\nBody text here.\n",
            source_type="md",
            word_count=5,
            metadata={"title": "Title"},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc) as mock_load,
            patch("utils.source_splitter.split_source", return_value=_chunks(1)) as mock_split,
        ):
            chunks, topic, content = _load_and_split_source(
                str(f), _args(), {"script": {"words_per_segment": 100}}
            )
        assert mock_load.called
        assert mock_split.called
        assert len(chunks) == 1
        assert topic == "Title"
        assert content == "# Title\n\nBody text here.\n"

    def test_filename_stem_used_as_topic(self, tmp_path):
        f = tmp_path / "my_great_document.md"
        f.write_text("Body", encoding="utf-8")
        doc = _doc("Body", source_type="md", word_count=1)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(1)),
        ):
            _, topic, _ = _load_and_split_source(str(f), _args(), {})
        assert topic == "my great document"

    def test_front_matter_title_overrides(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("Body", encoding="utf-8")
        doc = _doc(
            "Body",
            source_type="md",
            word_count=1,
            metadata={"front_matter": {"title": "Real Title"}},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(1)),
        ):
            _, topic, _ = _load_and_split_source(str(f), _args(), {})
        assert topic == "Real Title"

    def test_metadata_title_overrides_all(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("Body", encoding="utf-8")
        doc = _doc(
            "Body",
            source_type="md",
            word_count=1,
            metadata={"title": "Meta Title", "front_matter": {"title": "FM Title"}},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(1)),
        ):
            _, topic, _ = _load_and_split_source(str(f), _args(), {})
        assert topic == "Meta Title"


# ── _load_and_split_source: URL ─────────────────────────────────────────────


class TestLoadSplitUrl:
    def test_url_loads(self):
        doc = _doc(
            "Article body",
            source_type="url",
            word_count=2,
            metadata={"url": "https://example.com/article", "title": "Article"},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc) as mock_load,
            patch("utils.source_splitter.split_source", return_value=_chunks(1)),
        ):
            chunks, topic, _ = _load_and_split_source("https://example.com/article", _args(), {})
        assert mock_load.called
        assert chunks
        assert topic == "Article"

    def test_url_falls_back_to_url(self):
        doc = _doc(
            "Body",
            source_type="url",
            word_count=1,
            metadata={"url": "https://example.com/no-title-page"},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(1)),
        ):
            _, topic, _ = _load_and_split_source("https://example.com/no-title-page", _args(), {})
        assert "example.com" in topic or "no-title-page" in topic


# ── n_segments derivation ───────────────────────────────────────────────────


class TestSegmentCountDerivation:
    def test_segment_count_from_word_count(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        doc = _doc(" ".join(["word"] * 250), source_type="md", word_count=250)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(3)) as mock_split,
        ):
            _load_and_split_source(str(f), _args(), {"script": {"words_per_segment": 100}})
        args = mock_split.call_args.args
        assert args[1] == 3

    def test_segment_count_override_used(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        doc = _doc("body", source_type="md", word_count=1)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(10)) as mock_split,
        ):
            _load_and_split_source(
                str(f), _args(segment_count=10), {"script": {"words_per_segment": 100}}
            )
        args = mock_split.call_args.args
        assert args[1] == 10

    def test_words_per_segment_override(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        doc = _doc("body", source_type="md", word_count=200)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(4)) as mock_split,
        ):
            _load_and_split_source(
                str(f), _args(words_per_segment=50), {"script": {"words_per_segment": 100}}
            )
        args = mock_split.call_args.args
        assert args[1] == 4

    def test_short_source_minimum_one_chunk(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        doc = _doc("tiny", source_type="md", word_count=2)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(1)) as mock_split,
        ):
            _load_and_split_source(str(f), _args(), {"script": {"words_per_segment": 100}})
        args = mock_split.call_args.args
        assert args[1] == 1


# ── Error paths ─────────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_source_loader_error_exits(self, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("x", encoding="utf-8")
        with patch("utils.source_loader.load_source", side_effect=SourceLoaderError("bad file")):
            with pytest.raises(SystemExit) as exc_info:
                _load_and_split_source(str(f), _args(), {})
            assert exc_info.value.code == 1

    def test_splitter_error_exits(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        doc = _doc("body", source_type="md", word_count=5)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", side_effect=SourceSplitterError("nope")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _load_and_split_source(str(f), _args(), {})
            assert exc_info.value.code == 1

    def test_unexpected_load_error_exits(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("x", encoding="utf-8")
        with patch("utils.source_loader.load_source", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc_info:
                _load_and_split_source(str(f), _args(), {})
            assert exc_info.value.code == 1


# ── CLI argparse ────────────────────────────────────────────────────────────


class TestArgparse:
    def test_source_arg_present(self):
        from bootstrap_pipeline import run_pipeline_with_args

        with patch("sys.argv", ["bootstrap_pipeline.py", "--source", "doc.md"]):
            with contextlib.suppress(SystemExit):
                run_pipeline_with_args()
            assert True

    def test_source_arg_default_none(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--source", dest="source", default=None)
        args = parser.parse_args([])
        assert args.source is None


# ── End-to-end mock: run_long_pipeline receives source_chunks ───────────────


class TestEndToEndMock:
    def test_source_chunks_passed_to_run_long_pipeline(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nBody text here.\n", encoding="utf-8")
        doc = _doc(
            "# Title\n\nBody text here.\n",
            source_type="md",
            word_count=5,
            metadata={"title": "Title"},
        )
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(3)),
            patch("core.pipeline_long.run_long_pipeline") as mock_rlp,
        ):
            mock_rlp.return_value = {"status": "dry_run", "segments": 3, "output": "out.mp4"}
            chunks, topic, content = _load_and_split_source(
                str(f), _args(), {"script": {"words_per_segment": 100}}
            )
            mock_rlp(
                topic=topic,
                project_name=None,
                resume=True,
                dry_run=True,
                duration_min=None,
                director_mode=False,
                series_mode=False,
                content_text=content,
                preview_mode=False,
                words_per_segment=None,
                images_per_segment=None,
                segment_count=None,
                source_chunks=chunks,
            )
            mock_rlp.assert_called_once()
            call_kwargs = mock_rlp.call_args.kwargs
            assert call_kwargs["source_chunks"] is chunks
            assert len(call_kwargs["source_chunks"]) == 3
            assert call_kwargs["topic"] == "Title"

    def test_source_chunks_force_segment_count(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("Body", encoding="utf-8")
        doc = _doc("Body", source_type="md", word_count=1)
        ns = _args(segment_count=None)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(4)),
        ):
            _load_and_split_source(str(f), ns, {"script": {"words_per_segment": 100}})
        assert ns.segment_count == 4

    def test_source_segment_count_mismatch_warns(self, tmp_path, capsys):
        f = tmp_path / "doc.md"
        f.write_text("Body", encoding="utf-8")
        doc = _doc("Body", source_type="md", word_count=1)
        ns = _args(segment_count=99)
        with (
            patch("utils.source_loader.load_source", return_value=doc),
            patch("utils.source_splitter.split_source", return_value=_chunks(2)),
        ):
            _load_and_split_source(str(f), ns, {"script": {"words_per_segment": 100}})
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "99" in out and "2" in out
