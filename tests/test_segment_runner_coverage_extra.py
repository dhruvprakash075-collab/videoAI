from unittest.mock import MagicMock, patch

from core import segment_runner


def _process_kwargs(tmp_path, **overrides):
    import threading
    import time

    scheduler = MagicMock()
    scheduler.active_heavy_count = 0
    scheduler.task.return_value.__enter__.return_value = None
    scheduler.task.return_value.__exit__.return_value = False
    base = {
        "topic": "coverage topic",
        "config": {
            "critic": {"threshold": 60},
            "script": {"word_count_tolerance": 0.25, "words_per_segment": 50},
            "video": {"output_path": str(tmp_path / "final.mp4")},
            "checkpoint": {"enabled": True, "dir": str(tmp_path)},
            "tts": {"lang": "hi"},
            "performance": {"vram_evict_wait_s": 0},
        },
        "outline": [{"title": "Intro", "summary": "Summary", "mood": "calm"}],
        "n_segs": 1,
        "out_base": tmp_path,
        "tts_cfg": {"lang": "hi"},
        "cp_mgr": MagicMock(),
        "world_state": MagicMock(),
        "mem": MagicMock(),
        "ctx_mgr": MagicMock(),
        "director_agent_instance": MagicMock(),
        "writer_agent": MagicMock(),
        "resume": False,
        "dry_run": True,
        "fast_dry_run": False,
        "preview_mode": False,
        "words_per_seg": 50,
        "seg_min": 1,
        "shared_prompt_executor": MagicMock(),
        "global_scheduler": scheduler,
        "_crewai_lock": threading.RLock(),
        "crewai_lock": threading.RLock(),
        "completed_segs_counter_holder": [0],
        "completed_segs_lock": threading.Lock(),
        "mp4s": [None],
        "mp4s_lock": threading.Lock(),
        "run_start_ts": time.time(),
        "source_chunks": None,
    }
    base.update(overrides)
    return base


def test_ollama_active_timer_lifecycle():
    fake_timer = MagicMock()
    with patch("threading.Timer", return_value=fake_timer):
        segment_runner.schedule_ollama_stop({"ollama": {"host": "http://localhost:11434"}}, delay=1)
    fake_timer.start.assert_called_once()
    segment_runner.touch_ollama_active()
    fake_timer.cancel.assert_called_once()


def test_ollama_alive_true_and_false():
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=response):
        assert segment_runner._ollama_alive({"ollama": {"host": "http://localhost:11434"}}) is True
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert segment_runner._ollama_alive({"ollama": {"host": "http://localhost:11434"}}) is False


def test_tts_word_budget_and_trim_paths():
    assert segment_runner._tts_word_budget({}, 0, "hi") == 0
    assert segment_runner._tts_word_budget({"script": {"tts_words_per_minute_hi": 120}}, 30, "hi") == 60
    assert segment_runner._tts_word_budget({"script": {"tts_words_per_minute_en": 180}}, 20, "en") == 60

    assert segment_runner._trim_script_to_word_limit("one two", 5) == "one two"
    assert segment_runner._trim_script_to_word_limit("one two", 0) == "one two"
    assert segment_runner._trim_script_to_word_limit("One two. Three four five.", 3) == "One two."
    assert segment_runner._trim_script_to_word_limit("one two three four", 2) == "one two"


def test_stop_and_start_ollama_paths():
    with patch("sys.platform", "win32"), patch("subprocess.run") as run:
        segment_runner.stop_ollama_server({}, reason="test")
    assert run.call_args.args[0][0] == "taskkill"

    with patch("sys.platform", "linux"), patch("subprocess.run") as run:
        segment_runner.stop_ollama_server({}, reason="")
    assert run.call_args.args[0][0] == "pkill"

    with patch("subprocess.run", side_effect=RuntimeError("ignored")):
        segment_runner.stop_ollama_server({})

    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.Popen") as popen,
        patch("urllib.request.urlopen", return_value=response),
        patch("time.sleep"),
    ):
        assert segment_runner.start_ollama_server({"ollama": {"host": "http://localhost:11434"}})
    assert popen.called

    with patch("subprocess.Popen", side_effect=RuntimeError("boom")):
        assert not segment_runner.start_ollama_server({})

    with (
        patch("sys.platform", "linux"),
        patch("subprocess.Popen"),
        patch("urllib.request.urlopen", side_effect=OSError("down")),
        patch("time.sleep"),
    ):
        assert not segment_runner.start_ollama_server({"ollama": {"host": "http://localhost:11434"}})


def test_log_vram_and_cleanup_import_paths(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "torch", None)
    segment_runner.log_vram_usage("no torch")

    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False
    monkeypatch.setitem(__import__("sys").modules, "torch", mock_torch)
    sched = MagicMock(active_heavy_count=1)
    segment_runner.aggressive_vram_cleanup(sched)
    mock_torch.cuda.empty_cache.assert_not_called()


def test_preview_gate_ui_timeout_reject_approve_and_cli(monkeypatch):
    from agents.director_agent import UIState

    segment_runner.set_director_abort(False)
    UIState.is_ui_mode = True
    UIState.pause_event.clear()
    monkeypatch.setenv("DIRECTOR_TIMEOUT", "1")
    with patch.object(UIState.pause_event, "wait", return_value=False):
        segment_runner._preview_gate("seg.mp4", {})
    assert not segment_runner.get_director_abort()

    UIState.user_reply = "nope"
    with patch.object(UIState.pause_event, "wait", return_value=True):
        segment_runner._preview_gate("seg.mp4", {})
    assert segment_runner.get_director_abort()

    segment_runner.set_director_abort(False)
    UIState.user_reply = "approve"
    with patch.object(UIState.pause_event, "wait", return_value=True):
        segment_runner._preview_gate("seg.mp4", {})
    assert not segment_runner.get_director_abort()

    UIState.is_ui_mode = False
    with patch("sys.stdin.isatty", return_value=False), patch("builtins.print"):
        segment_runner._preview_gate(None, {})

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="q"),
        patch("builtins.print"),
    ):
        segment_runner._preview_gate("seg.mp4", {})
    assert segment_runner.get_director_abort()

    segment_runner.set_director_abort(False)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=EOFError),
        patch("builtins.print"),
    ):
        segment_runner._preview_gate("seg.mp4", {})
    assert not segment_runner.get_director_abort()
    UIState.is_ui_mode = False


def test_build_retry_wrapper_success_retry_and_degradation_failure():
    calls = []

    def flaky(i):
        calls.append(i)
        if len(calls) == 1:
            raise RuntimeError("first")

    retry_counts = {}
    wrapped = segment_runner.build_retry_wrapper(flaky, max_retries=1, segment_idx=1, retry_counts=retry_counts)
    wrapped(1)
    assert calls == [1, 1]
    assert retry_counts[1] == 1

    def always_fail(i):
        raise RuntimeError("boom")

    wrapped = segment_runner.build_retry_wrapper(always_fail, max_retries=0, segment_idx=2, retry_counts={})
    with patch("agents.director_agent.UIState.add_degradation") as add:
        wrapped(2)
    add.assert_called_once()

    wrapped = segment_runner.build_retry_wrapper(always_fail, max_retries=0, segment_idx=3, retry_counts={})
    with patch("agents.director_agent.UIState.add_degradation", side_effect=RuntimeError("ignored")):
        wrapped(3)


def test_make_process_segment_fast_dry_run_and_decision_record(tmp_path):
    class Duration:
        locked = True
        provenance = "user"
        value = 2

    decision = MagicMock(total_duration_min=Duration())
    blackboard = MagicMock()
    blackboard.read_decision.return_value = decision
    kwargs = _process_kwargs(tmp_path, fast_dry_run=True, dry_run=True)

    with patch("memory.blackboard.get_blackboard", return_value=blackboard):
        process, *_ = segment_runner.make_process_segment(**kwargs)
        process(1)

    kwargs["cp_mgr"].save.assert_any_call(
        "coverage topic_seg01",
        "script",
        {"data": "Intro. Summary This is a fast dry-run placeholder."},
    )
    kwargs["world_state"].update.assert_called()


def test_make_process_segment_decision_record_failure_and_source_chunk(tmp_path):
    chunk = MagicMock(index=0, text="source text for exact narration")
    kwargs = _process_kwargs(tmp_path, source_chunks=[chunk], dry_run=True)

    with patch("memory.blackboard.get_blackboard", side_effect=RuntimeError("no record")):
        process, *_ = segment_runner.make_process_segment(**kwargs)
        process(1)

    kwargs["cp_mgr"].save.assert_any_call(
        "coverage topic_seg01", "script", {"data": "source text for exact narration"}
    )


def test_make_process_segment_non_dry_image_review_and_memory(tmp_path):
    from PIL import Image

    img = tmp_path / "frame.png"
    Image.new("RGB", (10, 10), color="red").save(img)
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFF")
    mp4 = tmp_path / "segment_01.mp4"
    mp4.write_bytes(b"mp4")

    project = MagicMock()
    project.get_character_assets.return_value = {"identity_hash": "different"}
    mem = MagicMock()
    mem._project = project
    mem.read.return_value = {"memory_items": {"project": [{"a": 1}], "story": [{"b": 2}]}}

    director = MagicMock()
    director.translate_to_devanagari.return_value = "हिंदी कथा"
    director.review_important_image.return_value = {
        "decision": "lora_candidate",
        "reason": "good ref",
        "locked": True,
    }
    director.review_segment_memory.return_value = {"memory_items": [{"kind": "fact"}]}

    kwargs = _process_kwargs(
        tmp_path,
        dry_run=False,
        mem=mem,
        director_agent_instance=director,
        config={
            "critic": {"threshold": 60},
            "script": {"word_count_tolerance": 0.25, "words_per_segment": 50},
            "video": {"output_path": str(tmp_path / "final.mp4")},
            "checkpoint": {"enabled": True, "dir": str(tmp_path)},
            "tts": {"lang": "hi"},
            "image_gen": {"backend": "comfyui"},
            "subtitles": {"language": "hi"},
            "performance": {"vram_evict_wait_s": 0},
        },
        outline=[
            {
                "title": "Intro",
                "summary": "Summary",
                "mood": "calm",
                "char_presence": [{"hero": 0.9}],
            }
        ],
    )

    score = MagicMock(total=90, issues=[], suggestions=[])
    with (
        patch("utils.crewai_breaker.guarded_ollama_call", return_value='{"narration": "hello world"}'),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", return_value=score),
        patch("utils.critic.is_approved", return_value=True),
        patch("video.image_gen.image_gen.generate_images", return_value=[img]),
        patch("utils.scene_director.enrich_prompts", return_value=(["portrait full body"], "neg")),
        patch("audio.audio_proxy.tts_generate", return_value={"wav_path": str(wav)}),
        patch("utils.get_audio_duration", return_value=20),
        patch("video.renderer.renderer.render_with_assets", return_value=mp4),
        patch("video.image_gen.image_gen._free_comfyui_memory") as free_comfy,
        patch("memory.permanent_memory.PermanentMemoryLog") as perm,
        patch("torch.cuda.is_available", return_value=True),
        patch("torch.cuda.empty_cache"),
    ):
        process, *_ = segment_runner.make_process_segment(**kwargs)
        process(1)

    assert kwargs["mp4s"][0] == mp4
    project.record_asset_review.assert_called()
    director.review_segment_memory.assert_called_once()
    free_comfy.assert_called_once()
    perm.return_value.save_memory_item.assert_called_once_with({"kind": "fact"})


def test_make_process_segment_tts_does_not_budget_retry_or_truncate(tmp_path):
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFF")
    kwargs = _process_kwargs(tmp_path, dry_run=False)
    kwargs["tts_cfg"] = {"lang": "en"}
    kwargs["config"]["tts"] = {"lang": "en"}
    score = MagicMock(total=90, issues=[], suggestions=[])
    tts_calls = []

    def fake_tts(text, **kwargs):
        tts_calls.append(text)
        return {"wav_path": str(wav)}

    with (
        patch("utils.crewai_breaker.guarded_ollama_call", return_value='{"narration": "' + "word " * 80 + '"}'),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", return_value=score),
        patch("utils.critic.is_approved", return_value=True),
        patch("audio.audio_proxy.tts_generate", side_effect=fake_tts),
        patch("utils.get_audio_duration", return_value=999),
        patch("video.image_gen.image_gen.generate_images", return_value=[]),
        patch("video.renderer.renderer.render_with_assets", return_value=tmp_path / "out.mp4"),
        patch("torch.cuda.is_available", return_value=False),
    ):
        process, *_ = segment_runner.make_process_segment(**kwargs)
        process(1)

    assert len(tts_calls) == 1
    assert len(tts_calls[0].split()) >= 80


def test_returned_phase_functions_checkpoint_skips_and_render_phase(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"mp4")

    kwargs = _process_kwargs(tmp_path, resume=True, dry_run=True, fast_dry_run=True)

    def skip_ck(key):
        return {
            "script": {"data": "cached script"},
            "devanagari_script": {"data": "हिंदी", "script_for_tts": "cached script"},
            "world_state_applied": {"done": True},
            "audio": {"data": str(audio), "word_timestamps": None},
            "images": {"data": [str(image)]},
            "image_review_done": {"done": True},
            "video": {"data": str(video)},
            "render_done": {"done": True},
        }

    kwargs["cp_mgr"].get.side_effect = skip_ck
    (
        _process,
        run_scripts,
        run_translations,
        run_tts,
        run_images,
        run_renders,
    ) = segment_runner.make_process_segment(**kwargs)

    run_scripts([1])
    run_translations([1])
    run_tts([1])
    run_images([1])
    run_renders([1])

    kwargs["cp_mgr"].save.assert_not_called()

    def render_ck(key):
        return {
            "script": {"data": "cached script"},
            "audio": {"data": str(audio), "word_timestamps": None},
            "images": {"data": [str(image)]},
        }

    kwargs = _process_kwargs(tmp_path, resume=True, dry_run=True, fast_dry_run=True)
    kwargs["cp_mgr"].get.side_effect = render_ck
    (
        _process,
        _run_scripts,
        _run_translations,
        _run_tts,
        _run_images,
        run_renders,
    ) = segment_runner.make_process_segment(**kwargs)

    run_renders([1])
    kwargs["cp_mgr"].save.assert_any_call("coverage topic_seg01", "render_done", {"done": True})
    assert kwargs["completed_segs_counter_holder"][0] == 1


def test_phase_retry_budget_exhaustion_and_abort(tmp_path):
    kwargs = _process_kwargs(tmp_path, resume=False, dry_run=True, fast_dry_run=True)
    kwargs["config"].setdefault("performance", {})["max_segment_retries"] = 0
    kwargs["cp_mgr"].save.side_effect = RuntimeError("save failed")
    (
        _process,
        run_scripts,
        _run_translations,
        _run_tts,
        _run_images,
        _run_renders,
    ) = segment_runner.make_process_segment(**kwargs)

    with patch("agents.director_agent.UIState.add_degradation") as add:
        run_scripts([1])
    add.assert_called_once()

    segment_runner.set_director_abort(True)
    try:
        run_scripts([1])
    finally:
        segment_runner.set_director_abort(False)
