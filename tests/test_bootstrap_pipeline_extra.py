from argparse import Namespace
from unittest.mock import patch

import pytest

import bootstrap_pipeline as bp


def _args(**overrides):
    base = {
        "topic": "topic",
        "file": "",
        "source": None,
        "eval_models": False,
        "topics_file": None,
        "skip_preflight": True,
        "preflight_only": False,
        "dry_run": True,
        "project": None,
        "no_resume": False,
        "duration": None,
        "series": False,
        "preview": False,
        "words_per_segment": None,
        "images_per_segment": None,
        "segment_count": None,
        "yes": False,
    }
    base.update(overrides)
    return Namespace(**base)


def test_parser_accepts_core_flags():
    args = bp._build_parser().parse_args(
        [
            "--topic",
            "t",
            "--dry-run",
            "--no-resume",
            "--project",
            "p",
            "--series",
            "--preview",
            "--yes",
            "--duration",
            "2.5",
            "--words-per-segment",
            "120",
            "--images-per-segment",
            "3",
            "--segment-count",
            "4",
        ]
    )

    assert args.topic == "t"
    assert args.dry_run is True
    assert args.no_resume is True
    assert args.project == "p"
    assert args.series is True
    assert args.preview is True
    assert args.yes is True
    assert args.duration == 2.5
    assert args.words_per_segment == 120
    assert args.images_per_segment == 3
    assert args.segment_count == 4


def test_resolve_input_file_auto_topic_source_and_missing_input(tmp_path):
    story = tmp_path / "my_story.md"
    story.write_text(" body ", encoding="utf-8")

    topic, content, chunks = bp._resolve_input(_args(file=str(story)), {})
    assert topic == "my story"
    assert content == "body"
    assert chunks is None

    with patch("utils.topic_researcher.brainstorm_topic", return_value="researched"):
        assert bp._resolve_input(_args(topic="auto", skip_preflight=False), {"x": 1})[0] == "researched"

    with patch("utils.topic_researcher.brainstorm_topic", side_effect=RuntimeError("down")):
        assert bp._resolve_input(_args(topic="auto"), {})[0] == "The Mysteries of the Deep Ocean"

    with patch("bootstrap_pipeline._load_and_split_source", return_value=(["chunk"], "source title", "text")):
        assert bp._resolve_input(_args(source="doc.md"), {}) == ("source title", "text", ["chunk"])

    with pytest.raises(SystemExit) as exc:
        bp._resolve_input(_args(topic="", eval_models=False), {})
    assert exc.value.code == 2


def test_run_preflight_skip_success_failure_and_preflight_only():
    assert bp._run_preflight(_args(skip_preflight=True)) == (None, None)

    good = Namespace(all_ok=True)
    with (
        patch("config.load_config", return_value={"ok": True}),
        patch("utils.preflight.run_preflight", return_value=good),
    ):
        assert bp._run_preflight(_args(skip_preflight=False))[0] == {"ok": True}

    bad = Namespace(all_ok=False)
    with (
        patch("config.load_config", side_effect=RuntimeError("config")),
        patch("utils.preflight.run_preflight", return_value=bad),
    ):
        assert bp._run_preflight(_args(skip_preflight=False, dry_run=True))[0] == {}

    with (
        patch("config.load_config", return_value={}),
        patch("utils.preflight.run_preflight", return_value=bad),
        pytest.raises(SystemExit) as exc,
    ):
        bp._run_preflight(_args(skip_preflight=False, dry_run=False))
    assert exc.value.code == 1

    with (
        patch("config.load_config", return_value={}),
        patch("utils.preflight.run_preflight", return_value=bad),
        pytest.raises(SystemExit) as exc,
    ):
        bp._run_preflight(_args(skip_preflight=False, preflight_only=True))
    assert exc.value.code == 1

    with (
        patch("config.load_config", return_value={}),
        patch("utils.preflight.run_preflight", side_effect=RuntimeError("boom")),
    ):
        assert bp._run_preflight(_args(skip_preflight=False))[1] is None


def test_register_shutdown_hook_runs_and_swallows_errors():
    hooks = []

    with patch("utils.shutdown.register_cleanup_hook", side_effect=hooks.append):
        bp._register_shutdown_hook({"cfg": True})

    assert hooks[0].__name__ == "evict_ollama_on_shutdown"
    with patch("core.segment_runner.evict_ollama_models") as evict:
        hooks[0]()
    evict.assert_called_once()

    with patch("utils.shutdown.register_cleanup_hook", side_effect=RuntimeError("down")):
        bp._register_shutdown_hook({})


def test_run_single_success_dry_run_error_keyboard_and_exception():
    args = _args()

    assert bp._run_single(args, lambda **_: {"status": "success", "output": "out", "segments": 1, "duration_s": 2.0}, "t", None, None) == 0
    assert bp._run_single(args, lambda **_: {"status": "dry_run", "output": "out", "segments": 1}, "t", None, None) == 0
    assert bp._run_single(args, lambda **_: {"status": "failed", "reason": "bad"}, "t", None, None) == 1

    def keyboard(**_):
        raise KeyboardInterrupt

    def boom(**_):
        raise RuntimeError("boom")

    assert bp._run_single(args, keyboard, "t", None, None) == 1
    assert bp._run_single(args, boom, "t", None, None) == 1


def test_run_batch_records_success_and_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = _args(project="p", dry_run=True)

    def run_long_pipeline(topic, **_kwargs):
        if topic == "bad":
            raise RuntimeError("broken")
        return {"status": "dry_run", "output": "out"}

    report, total = bp._run_batch(args, run_long_pipeline, ["good", "bad"], ["chunk"])

    assert total == 2
    assert report[0]["status"] == "dry_run"
    assert report[1]["status"] == "error"
    assert (tmp_path / "studio_outputs" / "batch_report.json").exists()


def test_run_pipeline_with_args_eval_models_and_yes_mode():
    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--eval-models", "--skip-preflight"]),
        patch("core.pipeline_long.run_long_pipeline"),
        patch("utils.model_eval.run_eval") as run_eval,
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 0
    run_eval.assert_called_once()

    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--topic", "t", "--dry-run", "--skip-preflight", "--yes"]),
        patch("core.pipeline_long.run_long_pipeline", return_value={"status": "dry_run", "segments": 1, "output": "out"}),
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 0


def test_run_pipeline_with_args_topics_file_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    topics = tmp_path / "topics.txt"
    topics.write_text("# skip\none\n\ntwo\n", encoding="utf-8")

    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--topics-file", str(topics), "--dry-run", "--skip-preflight"]),
        patch("core.pipeline_long.run_long_pipeline", return_value={"status": "dry_run", "output": "out"}),
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 0

    missing = tmp_path / "missing.txt"
    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--topics-file", str(missing), "--skip-preflight"]),
        patch("core.pipeline_long.run_long_pipeline"),
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 1

    empty = tmp_path / "empty.txt"
    empty.write_text("# only comments\n\n", encoding="utf-8")
    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--topics-file", str(empty), "--skip-preflight"]),
        patch("core.pipeline_long.run_long_pipeline"),
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 1


def test_run_pipeline_with_args_import_error():
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "core.pipeline_long":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    with (
        patch("sys.argv", ["bootstrap_pipeline.py", "--topic", "t", "--skip-preflight"]),
        patch("builtins.__import__", side_effect=fake_import),
        pytest.raises(SystemExit) as exc,
    ):
        bp.run_pipeline_with_args()
    assert exc.value.code == 1
