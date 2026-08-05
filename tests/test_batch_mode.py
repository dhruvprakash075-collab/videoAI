"""test_batch_mode.py - Tests for D4: batch mode --topics-file (real bootstrap paths)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_pipeline as bp


def _write_topics_file(tmp_path, lines):
    p = tmp_path / "topics.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_topics_file_parsed_correctly(tmp_path):
    """Topics file should skip blank lines and # comments."""
    topics_file = _write_topics_file(
        tmp_path,
        [
            "Topic One",
            "",
            "# This is a comment",
            "Topic Two",
            "  ",
            "Topic Three",
        ],
    )
    assert bp._load_topics_file(topics_file) == ["Topic One", "Topic Two", "Topic Three"]


def test_topics_file_iteration_order(tmp_path):
    """Topics should be processed in file order."""
    topics_file = _write_topics_file(tmp_path, ["Alpha", "Beta", "Gamma"])
    assert bp._load_topics_file(topics_file) == ["Alpha", "Beta", "Gamma"]


def test_empty_topics_file(tmp_path):
    """An empty topics file (only blanks/comments) should produce no topics."""
    topics_file = _write_topics_file(tmp_path, ["", "# comment", "  "])
    assert bp._load_topics_file(topics_file) == []


def test_missing_topics_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bp._load_topics_file(tmp_path / "nope.txt")


def _fake_args():
    return SimpleNamespace(
        project=None,
        no_resume=False,
        dry_run=True,
        duration=None,
        series=False,
        preview=False,
        words_per_segment=None,
        images_per_segment=None,
        segment_count=None,
        no_storyboard=False,
        force_storyboard=False,
    )


def test_batch_report_structure_and_continue_on_failure(tmp_path, monkeypatch):
    """_run_batch should record per-topic report entries and continue on failure."""
    monkeypatch.chdir(tmp_path)

    def fake_run_pipeline(topic, **kwargs):
        if topic == "Bad Topic":
            raise RuntimeError("Simulated failure")
        return {"status": "success", "output": f"/out/{topic}.mp4"}

    report, total = bp._run_batch(
        _fake_args(), fake_run_pipeline, ["Good Topic", "Bad Topic", "Another Good Topic"], None
    )

    assert total == 3
    assert [r["status"] for r in report] == ["success", "error", "success"]
    assert [r["topic"] for r in report] == [
        "Good Topic",
        "Bad Topic",
        "Another Good Topic",
    ]
    assert report[0]["output"] == "/out/Good Topic.mp4"
    assert "degradations" in report[0] and "wall_time_s" in report[0]
    assert "error" in report[1]
    assert report[1]["error"] == "Simulated failure"
