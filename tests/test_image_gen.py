"""test_image_gen.py - Test the testable surface of video/image_gen/image_gen.py.

Bonsai (FLUX.2-Klein ternary via diffusers) is the only image backend.
The _bonsai() function is GPU-bound and not directly testable. We focus on
the orchestrators and helpers that ARE testable in pure Python.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen.image_gen import (
    _prompt_cache_key,
    _record_oom_event,
    _resolve_dominant_char,
    clear_oom_events,
    generate_images,
    get_oom_report,
    unload_bonsai_pipeline,
)


@pytest.fixture(autouse=True)
def _reset_oom():
    clear_oom_events()
    yield
    clear_oom_events()


# ── OOM ledger ───────────────────────────────────────────────────────────────


def test_record_oom_event_appends():
    _record_oom_event(
        {"image_index": 0, "tier_failed": 1, "fallback_tier": 2, "steps_used": 2}
    )
    report = get_oom_report()
    assert len(report) == 1
    assert report[0]["tier_failed"] == 1


def test_get_oom_report_returns_copy():
    _record_oom_event({"x": 1})
    report = get_oom_report()
    report.append({"y": 2})
    # Original is unchanged
    assert len(get_oom_report()) == 1


def test_clear_oom_events():
    _record_oom_event({"x": 1})
    _record_oom_event({"x": 2})
    assert len(get_oom_report()) == 2
    clear_oom_events()
    assert get_oom_report() == []


# ── unload_bonsai_pipeline ───────────────────────────────────────────────────


def test_unload_bonsai_when_no_pipeline(monkeypatch):
    """If pipeline is None, no error, just a debug log."""
    import video.image_gen.image_gen as mod

    monkeypatch.setattr(mod, "_bonsai_pipe", None)
    unload_bonsai_pipeline()  # Should not raise


def test_unload_bonsai_releases_pipeline(monkeypatch):
    """When a pipeline is loaded, it gets unloaded and VRAM cache cleared."""
    import video.image_gen.image_gen as mod

    fake_pipe = MagicMock()
    monkeypatch.setattr(mod, "_bonsai_pipe", fake_pipe)
    monkeypatch.setattr(mod, "_bonsai_model_id", "prism-ml/test-model")
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    unload_bonsai_pipeline()
    assert mod._bonsai_pipe is None
    assert mod._bonsai_model_id is None
    fake_torch.cuda.empty_cache.assert_called_once()


# ── _prompt_cache_key ────────────────────────────────────────────────────────


def test_prompt_cache_key_returns_8_chars():
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    key = _prompt_cache_key("a hero standing", cfg)
    assert len(key) == 8
    assert all(c in "0123456789abcdef" for c in key)


def test_prompt_cache_key_includes_master_portrait_hash():
    """Same prompt + params but different portrait hash should produce different keys."""
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k1 = _prompt_cache_key("a hero", cfg, master_portrait_hash="abc123")
    k2 = _prompt_cache_key("a hero", cfg, master_portrait_hash="def456")
    assert k1 != k2


def test_prompt_cache_key_differs_on_model_id():
    """Different bonsai models should not collide in cache."""
    cfg_a = {"bonsai_model": "model-a", "steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    cfg_b = {"bonsai_model": "model-b", "steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k1 = _prompt_cache_key("hero", cfg_a)
    k2 = _prompt_cache_key("hero", cfg_b)
    assert k1 != k2


def test_prompt_cache_key_uses_bonsai_defaults():
    """When cfg omits steps/width/etc., defaults match Bonsai spec (4/1024/1024/3.5)."""
    cfg = {}
    # Two identical calls must produce identical keys
    assert _prompt_cache_key("x", cfg) == _prompt_cache_key("x", cfg)


def test_prompt_cache_key_throttled_steps_included():
    """A throttled image must not be served as a full-quality cache hit."""
    cfg = {"steps": 4, "width": 1024, "height": 1024, "guidance_scale": 3.5}
    k_full = _prompt_cache_key("p", cfg, throttled_steps=4)
    k_throttled = _prompt_cache_key("p", cfg, throttled_steps=2)
    assert k_full != k_throttled


def test_prompt_cache_key_handles_list_prompt():
    cfg = {}
    k1 = _prompt_cache_key(["a", "b"], cfg)
    k2 = _prompt_cache_key("a;b", cfg)
    assert k1 == k2


# ── _resolve_dominant_char ───────────────────────────────────────────────────


def test_resolve_dominant_char_above_threshold():
    cp = {"marcus": 0.6, "elena": 0.2}
    key, weight = _resolve_dominant_char(cp)
    assert key == "marcus"
    assert weight == 0.6


def test_resolve_dominant_char_below_threshold():
    """Weight < 0.3 means no dominant char (env frame)."""
    cp = {"marcus": 0.2, "elena": 0.1}
    key, weight = _resolve_dominant_char(cp)
    assert key is None
    assert weight == 0.0


def test_resolve_dominant_char_empty():
    assert _resolve_dominant_char({}) == (None, 0.0)
    assert _resolve_dominant_char(None) == (None, 0.0)


def test_resolve_dominant_char_picks_max_above_threshold():
    cp = {"marcus": 0.4, "elena": 0.5}
    key, weight = _resolve_dominant_char(cp)
    assert key == "elena"
    assert weight == 0.5


# ── generate_images dispatcher ───────────────────────────────────────────────


def test_generate_images_string_prompts(tmp_path: Path):
    """Semicolon-separated string is split into list."""
    cfg = {"image_gen": {"backend": "bonsai"}}
    with patch("video.image_gen.image_gen._bonsai", return_value=[]) as bns:
        generate_images("a; b; c", tmp_path, cfg)
    prompts_arg = bns.call_args.args[0]
    assert len(prompts_arg) == 3


def test_generate_images_empty_list(tmp_path: Path):
    """Empty prompts list still gets dispatched."""
    cfg = {"image_gen": {"backend": "bonsai"}}
    with patch("video.image_gen.image_gen._bonsai", return_value=[]) as bns:
        generate_images([], tmp_path, cfg)
    assert bns.call_args.args[0] == []


def test_generate_images_passes_project_id(tmp_path: Path):
    """project_id is forwarded to _bonsai for project-scoped lookups."""
    cfg = {"image_gen": {"backend": "bonsai"}}
    with patch("video.image_gen.image_gen._bonsai", return_value=[]) as bns:
        generate_images(["p"], tmp_path, cfg, project_id="myproject")
    # _bonsai is called as _bonsai(prompts, out, cfg, char_presence=..., project_id=...)
    assert bns.call_args.kwargs.get("project_id") == "myproject"
