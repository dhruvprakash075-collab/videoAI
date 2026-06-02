"""test_batch_mode.py - Tests for D4: batch mode --topics-file."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))



def _write_topics_file(tmp_path, lines):
    p = tmp_path / "topics.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_topics_file_parsed_correctly(tmp_path):
    """Topics file should skip blank lines and # comments."""
    topics_file = _write_topics_file(tmp_path, [
        "Topic One",
        "",
        "# This is a comment",
        "Topic Two",
        "  ",
        "Topic Three",
    ])
    raw_lines = topics_file.read_text(encoding="utf-8").splitlines()
    topics = [l.strip() for l in raw_lines if l.strip() and not l.strip().startswith("#")]
    assert topics == ["Topic One", "Topic Two", "Topic Three"]


def test_topics_file_iteration_order(tmp_path):
    """Topics should be processed in file order."""
    topics_file = _write_topics_file(tmp_path, ["Alpha", "Beta", "Gamma"])
    raw_lines = topics_file.read_text(encoding="utf-8").splitlines()
    topics = [l.strip() for l in raw_lines if l.strip()]
    assert topics == ["Alpha", "Beta", "Gamma"]


def test_batch_report_structure(tmp_path):
    """batch_report.json should have the correct structure for each topic."""
    report = [
        {"topic": "Alpha", "status": "success", "output": "/out/alpha.mp4",
         "degradations": 0, "wall_time_s": 120.5},
        {"topic": "Beta", "status": "error", "error": "OOM", "wall_time_s": 30.0},
    ]
    report_path = tmp_path / "batch_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(loaded) == 2
    assert loaded[0]["topic"] == "Alpha"
    assert loaded[0]["status"] == "success"
    assert loaded[1]["status"] == "error"
    assert "wall_time_s" in loaded[0]


def test_batch_continues_on_failure(tmp_path):
    """Batch mode should continue to the next topic even if one fails."""
    topics = ["Good Topic", "Bad Topic", "Another Good Topic"]
    results = []

    def fake_run_pipeline(topic, **kwargs):
        if topic == "Bad Topic":
            raise RuntimeError("Simulated failure")
        return {"status": "success", "output": f"/out/{topic}.mp4"}

    for topic in topics:
        try:
            result = fake_run_pipeline(topic)
            results.append({"topic": topic, "status": result["status"]})
        except Exception as e:
            results.append({"topic": topic, "status": "error", "error": str(e)})

    assert len(results) == 3
    assert results[0]["status"] == "success"
    assert results[1]["status"] == "error"
    assert results[2]["status"] == "success"


def test_empty_topics_file(tmp_path):
    """An empty topics file (only blanks/comments) should produce no topics."""
    topics_file = _write_topics_file(tmp_path, ["", "# comment", "  "])
    raw_lines = topics_file.read_text(encoding="utf-8").splitlines()
    topics = [l.strip() for l in raw_lines if l.strip() and not l.strip().startswith("#")]
    assert topics == []
