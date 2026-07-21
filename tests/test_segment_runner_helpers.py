"""test_segment_runner_helpers.py - tests for the small helpers in core/segment_runner.py."""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.segment_runner import (
    _director_aborted,
    _trim_script_to_word_limit,
    _tts_word_budget,
    aggressive_vram_cleanup,
    evict_ollama_models,
    get_director_abort,
    log_vram_usage,
    set_director_abort,
)


def test_tts_word_budget_respects_short_duration_and_language():
    assert _tts_word_budget({}, 15, "hi") == 25
    assert _tts_word_budget({}, 15, "en") == 37
    assert _tts_word_budget({}, 0.1, "hi") == 1


def test_tts_word_budget_uses_configured_rates():
    config = {"script": {"tts_words_per_minute_hi": 80, "tts_words_per_minute_en": 120}}
    assert _tts_word_budget(config, 30, "hi") == 40
    assert _tts_word_budget(config, 30, "en") == 60


def test_tts_word_budget_30s_default_hi_needs_50_words():
    assert _tts_word_budget({}, 30, "hi") == 50


def test_trim_script_hard_caps_run_on_sentence():
    script = " ".join(f"word{i}" for i in range(100))
    assert len(_trim_script_to_word_limit(script, 25).split()) == 25


def test_trim_script_prefers_sentence_boundary():
    script = "One two three. Four five six seven. Eight nine ten."
    assert _trim_script_to_word_limit(script, 7) == "One two three. Four five six seven."


@pytest.fixture(autouse=True)
def _reset_abort():
    set_director_abort(False)
    yield
    set_director_abort(False)


# ── abort flag ───────────────────────────────────────────────────────────────


def test_set_abort_true():
    set_director_abort(True)
    assert get_director_abort() is True


def test_set_abort_false():
    set_director_abort(True)
    set_director_abort(False)
    assert get_director_abort() is False


def test_set_abort_default_true():
    set_director_abort()  # default arg is True
    assert get_director_abort() is True


def test_director_aborted_alias():
    set_director_abort(True)
    assert _director_aborted() is True
    set_director_abort(False)
    assert _director_aborted() is False


def test_abort_thread_safe():
    """Concurrent set/get should not raise."""
    results = []

    def setter():
        for _ in range(100):
            set_director_abort(True)
            set_director_abort(False)

    def getter():
        for _ in range(100):
            results.append(_director_aborted())

    t1 = threading.Thread(target=setter)
    t2 = threading.Thread(target=getter)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # All reads should be boolean
    assert all(isinstance(r, bool) for r in results)


# ── evict_ollama_models ──────────────────────────────────────────────────────


def _no_vram_poll_torch():
    """Torch mock that exits the VRAM polling loop immediately (no cuda)."""
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False
    return fake_torch


def test_evict_calls_urlopen_for_each_model():
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
            "reviewer": "x",
            "translator": "y",
            "image_engineer": "z",
        },
    }
    with (
        patch("urllib.request.urlopen") as urlopen_mock,
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        evict_ollama_models(cfg, reason="test")
    # 5 models should have been evicted
    assert urlopen_mock.call_count == 5


def test_evict_deduplicates_models():
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {
            "director": "same-model",
            "writer": "same-model",  # duplicate
        },
    }
    with (
        patch("urllib.request.urlopen") as urlopen_mock,
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        evict_ollama_models(cfg)
    # Only 1 unique model should be evicted
    assert urlopen_mock.call_count == 1


def test_evict_handles_urlopen_failure():
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {"director": "hermes"},
    }
    with (
        patch("urllib.request.urlopen", side_effect=RuntimeError("network down")),
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        # Should not raise
        evict_ollama_models(cfg)


def test_evict_skips_empty_model_names():
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {
            "director": "",
            "writer": "zephyr",
        },
    }
    with (
        patch("urllib.request.urlopen") as urlopen_mock,
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        evict_ollama_models(cfg)
    # Only zephyr should be evicted
    assert urlopen_mock.call_count == 1


def test_evict_default_host():
    """If config omits ollama host, default is used."""
    cfg = {"models": {"director": "x"}}
    with (
        patch("urllib.request.urlopen") as urlopen_mock,
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        evict_ollama_models(cfg)
    # Should still call urlopen with default host
    assert urlopen_mock.call_count == 1


def test_evict_rejects_metadata_host():
    cfg = {
        "ollama": {"host": "http://169.254.169.254"},
        "models": {"director": "x"},
    }
    with (
        patch("urllib.request.urlopen") as urlopen_mock,
        patch.dict(sys.modules, {"torch": _no_vram_poll_torch()}),
    ):
        evict_ollama_models(cfg)
    urlopen_mock.assert_not_called()


def test_evict_uses_configured_vram_threshold():
    """If vram threshold is met immediately, return quickly."""
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "models": {"director": "x"},
        "performance": {"vram_evict_wait_s": 5, "vram_sd_threshold_gb": 2.0},
    }
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.return_value = (
        4 * 1024**3,
        6 * 1024**3,
    )  # 4GB free > 2GB threshold
    with patch("urllib.request.urlopen"), patch.dict(sys.modules, {"torch": fake_torch}):
        t0 = time.time()
        evict_ollama_models(cfg)
        elapsed = time.time() - t0
    # Should be near-instant (no polling loop needed)
    assert elapsed < 1.0


# ── log_vram_usage ───────────────────────────────────────────────────────────


def test_log_vram_usage_updates_uistate(monkeypatch):
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.return_value = (3 * 1024**3, 6 * 1024**3)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    log_vram_usage("test")
    from agents.director_agent import UIState

    assert "3.0" in UIState.vram_text or "3.0/" in UIState.vram_text or "/" in UIState.vram_text


def test_log_vram_usage_no_cuda():
    """When CUDA is not available, no error, no UIState update."""
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False
    with patch.dict(sys.modules, {"torch": fake_torch}):
        log_vram_usage()  # should not raise


def test_log_vram_usage_no_torch():
    """When torch isn't installed, no error."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        log_vram_usage()  # should not raise


def test_log_vram_usage_handles_exception():
    """If anything else goes wrong, no crash."""
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.side_effect = RuntimeError("cuda fail")
    with patch.dict(sys.modules, {"torch": fake_torch}):
        log_vram_usage()  # should not raise


# ── aggressive_vram_cleanup ──────────────────────────────────────────────────


def test_aggressive_cleanup_no_cuda():
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False
    sched = MagicMock()
    sched.active_heavy_count = 0
    with patch.dict(sys.modules, {"torch": fake_torch}):
        aggressive_vram_cleanup(sched)  # should not raise


def test_aggressive_cleanup_skipped_when_heavy_active():
    """If a heavy task is running, don't clear VRAM (it would corrupt it)."""
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    sched = MagicMock()
    sched.active_heavy_count = 1
    with patch.dict(sys.modules, {"torch": fake_torch}):
        aggressive_vram_cleanup(sched)
    # Should NOT have called empty_cache
    fake_torch.cuda.empty_cache.assert_not_called()


def test_aggressive_cleanup_clears_vram():
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    sched = MagicMock()
    sched.active_heavy_count = 0
    with patch.dict(sys.modules, {"torch": fake_torch}):
        aggressive_vram_cleanup(sched)
    fake_torch.cuda.empty_cache.assert_called_once()
    fake_torch.cuda.synchronize.assert_called_once()


def test_aggressive_cleanup_no_torch():
    sched = MagicMock()
    sched.active_heavy_count = 0
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        aggressive_vram_cleanup(sched)  # should not raise


# ── build_retry_wrapper ──────────────────────────────────────────────────────


def test_retry_wrapper_success_no_retry():
    """Process segment succeeds on first try — no retry."""
    from core.segment_runner import build_retry_wrapper

    calls = []

    def process_segment(i):
        calls.append(i)

    counts = {}
    wrapped = build_retry_wrapper(
        process_segment, max_retries=3, segment_idx=1, retry_counts=counts
    )
    wrapped(1)
    assert calls == [1]
    assert counts == {1: 0}  # unchanged on success


def test_retry_wrapper_retries_on_failure():
    """If process_segment fails, retry up to max_retries times."""
    from core.segment_runner import build_retry_wrapper

    attempts = []

    def process_segment(i):
        attempts.append(i)
        if len(attempts) < 3:
            raise RuntimeError("boom")

    counts = {}
    wrapped = build_retry_wrapper(
        process_segment, max_retries=5, segment_idx=1, retry_counts=counts
    )
    wrapped(1)
    # Called 3 times: 2 failures + 1 success
    assert len(attempts) == 3
    assert counts[1] == 2  # 2 failures counted


def test_retry_wrapper_exhausts_budget():
    """When retries are exhausted, the segment is skipped (returns)."""
    from core.segment_runner import build_retry_wrapper

    attempts = []

    def process_segment(i):
        attempts.append(i)
        raise RuntimeError("always fails")

    counts = {}
    wrapped = build_retry_wrapper(
        process_segment, max_retries=2, segment_idx=1, retry_counts=counts
    )
    wrapped(1)
    # Called max_retries + 1 = 3 times (initial + 2 retries)
    assert len(attempts) == 3
    assert counts[1] == 3  # 3 failures counted


def test_retry_wrapper_zero_retries():
    """With max_retries=0, fails immediately on first error."""
    from core.segment_runner import build_retry_wrapper

    attempts = []

    def process_segment(i):
        attempts.append(i)
        raise RuntimeError("boom")

    counts = {}
    wrapped = build_retry_wrapper(
        process_segment, max_retries=0, segment_idx=1, retry_counts=counts
    )
    wrapped(1)
    # Only 1 attempt (no retries allowed)
    assert len(attempts) == 1
    assert counts[1] == 1


def test_retry_wrapper_preserves_existing_count():
    """If the count is already set, it continues from there."""
    from core.segment_runner import build_retry_wrapper

    attempts = []

    def process_segment(i):
        attempts.append(i)
        raise RuntimeError("boom")

    counts = {1: 1}  # already 1 prior failure
    wrapped = build_retry_wrapper(
        process_segment, max_retries=2, segment_idx=1, retry_counts=counts
    )
    wrapped(1)
    # Now should be 1+2+1 = 4 total attempts (initial 1 already in count + 2 retries + 1 more attempt?)
    # Actually the wrapper counts failures. So:
    # - i=1, retry_counts[1]=1, attempts=1, raise, retry_counts[1]=2
    # - i=1, retry_counts[1]=2, attempts=2, raise, retry_counts[1]=3 > 2 → exit
    # Total attempts = 2, count = 3
    assert len(attempts) == 2
    assert counts[1] == 3


# ── _preview_gate ────────────────────────────────────────────────────────────


def test_preview_gate_ui_mode_approve(monkeypatch):
    """In UI mode, 'approve' reply does NOT set the abort flag."""
    from agents.director_agent import UIState
    from core.segment_runner import _preview_gate, get_director_abort

    UIState.is_ui_mode = True
    UIState.user_reply = "approve"

    def fake_wait(timeout=0):
        return True

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    _preview_gate(Path("C:/tmp/seg1.mp4"), {})
    assert get_director_abort() is False
    UIState.is_ui_mode = False


def test_preview_gate_ui_mode_reject_aborts(monkeypatch):
    """In UI mode, non-approve reply sets the abort flag."""
    from agents.director_agent import UIState
    from core.segment_runner import _preview_gate, get_director_abort, set_director_abort

    set_director_abort(False)
    UIState.is_ui_mode = True
    UIState.user_reply = "no, looks bad"

    def fake_wait(timeout=0):
        return True

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    _preview_gate(Path("C:/tmp/seg1.mp4"), {})
    assert get_director_abort() is True
    UIState.is_ui_mode = False
    set_director_abort(False)


def test_preview_gate_ui_mode_timeout_proceeds(monkeypatch):
    """In UI mode, pause timeout → no abort, just proceed."""
    from agents.director_agent import UIState
    from core.segment_runner import _preview_gate, get_director_abort

    UIState.is_ui_mode = True

    def fake_wait(timeout=0):
        return False

    monkeypatch.setattr(UIState.pause_event, "wait", fake_wait)
    _preview_gate(Path("C:/tmp/seg1.mp4"), {})
    assert get_director_abort() is False
    UIState.is_ui_mode = False


def test_preview_gate_non_tty_auto_approves(monkeypatch):
    """Non-TTY → auto-approve, no abort."""
    from agents.director_agent import UIState
    from core.segment_runner import _preview_gate, get_director_abort

    UIState.is_ui_mode = False
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _preview_gate(Path("C:/tmp/seg1.mp4"), {})
    assert get_director_abort() is False


def test_preview_gate_none_path(monkeypatch):
    """When mp4_path is None, still works with placeholder string."""
    from agents.director_agent import UIState
    from core.segment_runner import _preview_gate, get_director_abort

    UIState.is_ui_mode = False
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _preview_gate(None, {})
    assert get_director_abort() is False


# ── process_segment closure tests ─────────────────────────────────────


def test_make_process_segment_creates_closure(tmp_path):
    """make_process_segment builds a closure without errors (minimal deps)."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler

    mp4s = [None] * 3
    counter = [0]
    cfg = {"critic": {"threshold": 60}, "script": {"word_count_tolerance": 0.25}}
    outline = [{"title": "Intro"}, {"title": "Body"}, {"title": "End"}]

    process_seg, *_ = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=3,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),

        resume=False,
        dry_run=True,
        fast_dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,  # Use real scheduler
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
    )
    assert callable(process_seg)


def test_process_segment_source_chunk_short_circuits(tmp_path):
    """When source_chunk is provided, process_seg runs without invoking the writer LLM."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler
    from utils.source_splitter import SegmentChunk

    mp4s = [None]
    counter = [0]
    cfg = {"critic": {"threshold": 60}, "script": {"word_count_tolerance": 0.25}}
    outline = [{"title": "Intro"}]

    process_seg, *_ = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),

        resume=False,
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
        source_chunks=[
            SegmentChunk(index=1, text="verbatim source text", source_chapter="Chapter 1")
        ],
    )

    # Should not raise even though writer_agent is a MagicMock (source-chunk path skips writer)
    result = process_seg(1)
    # process_segment returns None (void function)
    assert result is None


def test_process_segment_no_source_chunk_dry_run(tmp_path):
    """When no source_chunk and dry_run=True, process_seg returns without error."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler

    mp4s = [None]
    counter = [0]
    cfg = {"critic": {"threshold": 60}, "script": {"word_count_tolerance": 0.25}}
    outline = [{"title": "Intro"}]

    process_seg, *_ = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),

        resume=False,
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
    )

    with (
        patch("crewai.Crew"),
        patch("crewai.Task"),
        patch(
            "utils.crewai_breaker.guarded_ollama_call",
            return_value='{"narration": "Short dry run narration."}',
        ),
        patch("core.segment_runner.log_vram_usage"),
        patch("core.segment_runner.aggressive_vram_cleanup"),
    ):
        result = process_seg(1)
    assert result is None


def test_preview_gate_cli_approve(monkeypatch):
    """In CLI mode, empty choice approves."""
    from core.segment_runner import _preview_gate, get_director_abort, set_director_abort

    set_director_abort(False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "")
    _preview_gate(Path("C:/x.mp4"), {})
    assert get_director_abort() is False


def test_preview_gate_cli_abort(monkeypatch):
    """In CLI mode, choice 'q' aborts."""
    from core.segment_runner import _preview_gate, get_director_abort, set_director_abort

    set_director_abort(False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "q")
    try:
        _preview_gate(Path("C:/x.mp4"), {})
        assert get_director_abort() is True
    finally:
        set_director_abort(False)


def test_preview_gate_cli_eof_error(monkeypatch):
    """In CLI mode, EOFError auto-approves."""
    from core.segment_runner import _preview_gate, get_director_abort, set_director_abort

    set_director_abort(False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def mock_input(*args):
        raise EOFError()

    monkeypatch.setattr("builtins.input", mock_input)
    _preview_gate(Path("C:/x.mp4"), {})
    assert get_director_abort() is False


def test_preview_gate_cli_keyboard_interrupt(monkeypatch):
    """In CLI mode, KeyboardInterrupt auto-approves."""
    from core.segment_runner import _preview_gate, get_director_abort, set_director_abort

    set_director_abort(False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def mock_input(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", mock_input)
    _preview_gate(Path("C:/x.mp4"), {})
    assert get_director_abort() is False


# ── VRAM and fallbacks ───────────────────────────────────────────────────────


def test_evict_ollama_models_poll_low_vram_and_ps():
    """VRAM remains low, so api/ps fallback is invoked."""
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "performance": {"vram_evict_wait_s": 0.1, "vram_sd_threshold_gb": 10.0},
    }
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.return_value = (1 * 1024**3, 12 * 1024**3)

    import io

    # Mock response for /api/ps
    ps_response = io.BytesIO(b'{"models": [{"name": "hermes"}]}')

    def mock_urlopen(req, *args, **kwargs):
        if (hasattr(req, "full_url") and "/api/ps" in req.full_url) or (
            isinstance(req, str) and "/api/ps" in req
        ):
            return ps_response
        return MagicMock()

    with (
        patch("urllib.request.urlopen", side_effect=mock_urlopen) as mock_open,
        patch.dict(sys.modules, {"torch": fake_torch}),
    ):
        evict_ollama_models(cfg)
        assert mock_open.call_count >= 1


def test_evict_ollama_models_poll_low_vram_and_ps_fails():
    """VRAM is low, and api/ps API fails (does not raise)."""
    cfg = {
        "ollama": {"host": "http://localhost:11434"},
        "performance": {"vram_evict_wait_s": 0.05, "vram_sd_threshold_gb": 10.0},
    }
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.mem_get_info.return_value = (1 * 1024**3, 12 * 1024**3)

    with (
        patch("urllib.request.urlopen", side_effect=Exception("network error")),
        patch.dict(sys.modules, {"torch": fake_torch}),
    ):
        evict_ollama_models(cfg)  # should not raise


def test_log_vram_usage_importerror():
    """When torch raises ImportError, log_vram_usage does not crash."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        log_vram_usage()  # should not raise


# ── Budget & Exceptions ──────────────────────────────────────────────────────


def test_process_segment_aborted_early(tmp_path):
    """When global abort flag is set, process_segment returns early."""
    from core.segment_runner import make_process_segment, set_director_abort
    from utils.concurrency import global_scheduler

    set_director_abort(True)
    try:
        mp4s = [None]
        counter = [0]
        process_seg, *_ = make_process_segment(
            topic="test",
            config={},
            outline=[{"title": "Intro"}],
            n_segs=1,
            out_base=tmp_path,
            tts_cfg={},
            cp_mgr=MagicMock(),
            world_state=MagicMock(),
            mem=MagicMock(),
            ctx_mgr=MagicMock(),
            director_agent_instance=MagicMock(),
            writer_agent=MagicMock(),

            resume=False,
            dry_run=True,
            preview_mode=False,
            words_per_seg=100,
            seg_min=2,
            shared_prompt_executor=MagicMock(),
            global_scheduler=global_scheduler,
            _crewai_lock=threading.RLock(),
            crewai_lock=threading.RLock(),
            completed_segs_counter_holder=counter,
            completed_segs_lock=threading.Lock(),
            mp4s=mp4s,
            mp4s_lock=threading.Lock(),
            run_start_ts=time.time(),
        )
        process_seg(1)
        assert counter[0] == 0
    finally:
        set_director_abort(False)


def test_process_segment_no_ctx_mgr(tmp_path):
    """When ctx_mgr is None, process_seg uses fallback memory context."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler

    mp4s = [None]
    counter = [0]
    mem_mock = MagicMock()
    mem_mock.load.return_value = []

    process_seg, *_ = make_process_segment(
        topic="test",
        config={"performance": {"vram_evict_wait_s": 0}},
        outline=[{"title": "Intro"}],
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=mem_mock,
        ctx_mgr=None,  # trigger fallback
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),

        resume=False,
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
    )
    with (
        patch("crewai.Crew"),
        patch("crewai.Task"),
        patch("core.segment_runner._ollama_alive", return_value=True),
        patch("utils.crewai_breaker.guarded_ollama_call", return_value='{"narration": "Short test narration."}'),
        patch("utils.validate_script", return_value=True),
        patch("utils.critic.score_script", return_value=MagicMock(total=80, issues=[], suggestions=[])),
    ):
        process_seg(1)
    assert counter[0] == 1


def test_process_segment_exception_handling_resume(tmp_path):
    """If segment fails and resume=True, exception is skipped."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler

    mp4s = [None]
    counter = [0]
    ws_mock = MagicMock()
    ws_mock.to_prompt_block.side_effect = Exception("failed to load ws")

    process_seg, *_ = make_process_segment(
        topic="test",
        config={},
        outline=[{"title": "Intro"}],
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=ws_mock,
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),

        resume=True,  # triggers skip rather than raise
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
    )
    process_seg(1)
    assert counter[0] == 1  # completes (finishes cleanup even on failure)


def test_build_retry_wrapper_exhausted():
    """Retry budget exhausted logs warning and adds degradation."""
    from core.segment_runner import build_retry_wrapper

    def failing_segment(i):
        raise RuntimeError("always fails")

    counts = {}
    wrapped = build_retry_wrapper(
        failing_segment, max_retries=1, segment_idx=1, retry_counts=counts
    )
    # Mock UIState to verify degradation reporting
    from agents.director_agent import UIState

    degradations = []

    def mock_add_degradation(idx, type_str, desc):
        degradations.append((idx, type_str, desc))

    with patch.object(UIState, "add_degradation", mock_add_degradation):
        wrapped(1)

    assert counts[1] == 2  # try 0, try 1 (total 2 attempts)
    assert len(degradations) == 1
    assert degradations[0][0] == 1
    assert degradations[0][1] == "segment_skip"


# ── TTS duration guard / word budget trim ─────────────────────────────────────


def test_write_script_node_trims_over_long_script_to_word_budget(tmp_path):
    """write_script_node calls _trim_script_to_word_limit with cap = min(tts_budget, hi)."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler

    mp4s = [None]
    counter = [0]
    cfg = {
        "critic": {"threshold": 60},
        "script": {"word_count_tolerance": 0.25},
        "narrator": {"lang": "hi"},
    }
    outline = [{"title": "Intro"}]

    process_seg, *_ = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),
        resume=False,
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
    )

    # 300 words — way over the 125-word cap (hi=125, tts_budget=200)
    long_script = " ".join(["word"] * 300) + "."

    with (
        patch("crewai.Crew"),
        patch("crewai.Task"),
        patch(
            "utils.crewai_breaker.guarded_ollama_call",
            return_value=f'{{"narration": "{long_script}"}}',
        ),
        patch("core.segment_runner.log_vram_usage"),
        patch("core.segment_runner.aggressive_vram_cleanup"),
        patch(
            "core.segment_runner._trim_script_to_word_limit",
            wraps=_trim_script_to_word_limit,
        ) as spy_trim,
    ):
        process_seg(1)

    assert spy_trim.call_count >= 1
    # seg_min=2 → 120s → hi@100wpm → 200 word budget
    # hi = words_per_seg * 1.25 = 125
    # cap = min(200, 125) = 125
    _call_args = spy_trim.call_args_list[0]
    assert _call_args[0][1] == 125, f"expected cap=125, got {_call_args[0][1]}"


def test_write_script_node_trims_source_chunk_to_word_budget(tmp_path):
    """Source chunk text that exceeds budget is trimmed."""
    from core.segment_runner import make_process_segment
    from utils.concurrency import global_scheduler
    from utils.source_splitter import SegmentChunk

    mp4s = [None]
    counter = [0]
    cfg = {
        "critic": {"threshold": 60},
        "script": {"word_count_tolerance": 0.25},
        "narrator": {"lang": "hi"},
    }
    outline = [{"title": "Intro"}]

    process_seg, *_ = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=1,
        out_base=tmp_path,
        tts_cfg={},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=MagicMock(),
        writer_agent=MagicMock(),
        resume=False,
        dry_run=True,
        preview_mode=False,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=global_scheduler,
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=time.time(),
        source_chunks=[
            SegmentChunk(index=1, text=" ".join(["word"] * 300) + ".", source_chapter="Chapter 1")
        ],
    )

    with (
        patch("core.segment_runner.log_vram_usage"),
        patch("core.segment_runner.aggressive_vram_cleanup"),
        patch("core.segment_runner._trim_script_to_word_limit", wraps=_trim_script_to_word_limit) as spy_trim,
    ):
        process_seg(1)

    assert spy_trim.call_count >= 1
    _cap = spy_trim.call_args_list[0][0][1]
    assert _cap == 125, f"expected cap=125, got {_cap}"
