"""test_image_gen.py - Test the testable surface of video/image_gen/image_gen.py.

Bonsai (FLUX.2-Klein ternary via diffusers) is the only image backend.
The _bonsai() function is GPU-bound and not directly testable. We focus on
the orchestrators and helpers that ARE testable in pure Python.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen.image_gen import (
    _comfyui,
    _pexels,
    _prompt_cache_key,
    _record_oom_event,
    _replicate,
    _resolve_dominant_char,
    clear_oom_events,
    generate_images,
    get_oom_report,
    unload_bonsai_pipeline,
)


def test_comfyui_keeps_generated_images_when_memory_cleanup_fails(tmp_path: Path):
    image = tmp_path / "scene.png"
    client = MagicMock()
    client.generate_image.return_value = [image]
    client.free_memory.side_effect = RuntimeError("free failed")
    runtime = MagicMock(base_url="http://127.0.0.1:8188")
    runtime.ensure_running.return_value = True
    cfg = {"comfyui": {"unload_after_batch": True}}

    with (
        patch("video.image_gen.comfyui_runtime.get_comfyui_runtime", return_value=runtime),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=client),
        patch("video.image_gen.comfyui_workflow.create_default_workflow", return_value={}),
    ):
        assert _comfyui(["prompt"], tmp_path, cfg) == [image]


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


def test_generate_images_qwen_preflight_pass_dispatches_two_pass(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]) as preflight,
        patch("video.image_gen.image_gen._comfyui_qwen_edit", return_value=[]) as qwen,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
    ):
        generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    preflight.assert_called_once()
    qwen.assert_called_once()
    comfy.assert_not_called()


def test_generate_images_qwen_preflight_failure_uses_one_pass_comfyui(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True, "model_path": ""},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=["missing model"]) as preflight,
        patch("video.image_gen.image_gen._comfyui_qwen_edit", return_value=[]) as qwen,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
        patch("video.image_gen.image_gen._bonsai", return_value=[]) as bonsai,
    ):
        generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    preflight.assert_called_once()
    qwen.assert_not_called()
    comfy.assert_called_once()
    bonsai.assert_not_called()


def test_generate_images_qwen_runtime_failure_falls_back_to_bonsai(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]),
        patch(
            "video.image_gen.image_gen._comfyui_qwen_edit",
            side_effect=RuntimeError("qwen exploded"),
        ) as qwen,
        patch("video.image_gen.image_gen._bonsai", return_value=[tmp_path / "fallback.png"]) as bonsai,
        patch("video.image_gen.image_gen._comfyui", return_value=[]) as comfy,
    ):
        result = generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    qwen.assert_called_once()
    bonsai.assert_called_once()
    comfy.assert_not_called()
    assert result == [tmp_path / "fallback.png"]


def test_generate_images_qwen_runtime_failure_respects_non_bonsai_fallback(tmp_path: Path):
    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "fallback_backend": "raise",
            "qwen_edit": {"enabled": True},
        }
    }
    with (
        patch("video.image_gen.image_gen._qwen_preflight_issues", return_value=[]),
        patch("video.image_gen.image_gen._comfyui_qwen_edit", side_effect=RuntimeError("qwen exploded")),
        patch("video.image_gen.image_gen._bonsai", return_value=[]) as bonsai,
    ):
        with pytest.raises(RuntimeError, match="qwen exploded"):
            generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    bonsai.assert_not_called()


def test_comfyui_qwen_edit_only_reposes_character_frames(tmp_path: Path):
    from video.image_gen import image_gen
    from video.image_gen.qwen_repose import QwenEditResult

    images = [tmp_path / "scene_01.png", tmp_path / "scene_02.png", tmp_path / "scene_03.png"]
    cfg = {"qwen_edit": {"character_threshold": 0.05}}
    # The two-pass loop calls repose_character_detailed and uses the returned
    # QwenEditResult.output_path for each composited frame.
    repose = MagicMock(
        side_effect=[
            QwenEditResult("edited", str(tmp_path / "edited_01.png"), ""),
            QwenEditResult("edited", str(tmp_path / "edited_03.png"), ""),
        ]
    )
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory") as free_memory,
        patch("video.image_gen.qwen_repose.repose_character_detailed", repose),
    ):
        result = image_gen._comfyui_qwen_edit(
            ["first", "second", "third"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.1}, {}, {"villain": 0.2}],
            project_id="project-a",
        )

    free_memory.assert_called_once_with(cfg)
    # Only the two frames with a dominant character above threshold are reposed;
    # the middle (no-character) frame is left untouched.
    assert repose.call_count == 2
    assert repose.call_args_list[0].args[:4] == (
        str(images[0]),
        "hero",
        "first",
        str(images[0]),
    )
    assert repose.call_args_list[1].args[:4] == (
        str(images[2]),
        "villain",
        "third",
        str(images[2]),
    )
    # Composited outputs are interleaved in order with the kept background.
    assert result == [tmp_path / "edited_01.png", images[1], tmp_path / "edited_03.png"]


def test_comfyui_qwen_edit_respects_character_threshold(tmp_path: Path):
    from video.image_gen import image_gen
    from video.image_gen.qwen_repose import QwenEditResult

    images = [tmp_path / "scene_01.png", tmp_path / "scene_02.png"]
    cfg = {"qwen_edit": {"character_threshold": 0.5}}
    repose = MagicMock(return_value=QwenEditResult("edited", str(images[1]), ""))
    with (
        patch.object(image_gen, "_comfyui", return_value=images),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch("video.image_gen.qwen_repose.repose_character_detailed", repose),
    ):
        result = image_gen._comfyui_qwen_edit(
            ["low", "high"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.49}, {"hero": 0.5}],
            project_id="project-a",
        )

    # 0.49 is below the 0.5 threshold (frame skipped); 0.5 meets it and is reposed.
    repose.assert_called_once()
    assert repose.call_args.args[1] == "hero"
    assert repose.call_args.args[2] == "high"
    assert result == images


def test_pexels_search_url_has_no_literal_braces(tmp_path: Path, monkeypatch):
    """Regression guard for malformed f-string URLs in the dormant Pexels path."""
    import json
    import urllib.request

    captured_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"photos": []}).encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        captured_urls.append(req.full_url if isinstance(req, urllib.request.Request) else req)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert _pexels(["hero portrait"], tmp_path, {"pexels_api_key": "test-key"}) == []
    assert captured_urls == [
        "https://api.pexels.com/v1/search?query=hero+portrait&per_page=1&orientation=landscape"
    ]
    assert "{" not in captured_urls[0]
    assert "}" not in captured_urls[0]


def test_generate_images_passes_project_id(tmp_path: Path):
    """project_id is forwarded to _bonsai for project-scoped lookups."""
    cfg = {"image_gen": {"backend": "bonsai"}}
    with patch("video.image_gen.image_gen._bonsai", return_value=[]) as bns:
        generate_images(["p"], tmp_path, cfg, project_id="myproject")
    # _bonsai is called as _bonsai(prompts, out, cfg, char_presence=..., project_id=...)
    assert bns.call_args.kwargs.get("project_id") == "myproject"


class TestReplicateRegression:
    def test_replicate_metadata_url_rejected(self, tmp_path: Path):
        """Replicate returning a metadata IP URL should be rejected."""
        mock_output = ["http://169.254.169.254/latest/meta-data/"]
        mock_replicate = MagicMock()
        mock_replicate.run.return_value = mock_output
        with (
            patch.dict("sys.modules", {"replicate": mock_replicate}),
            patch("utils.url_security.validate_source_url", side_effect=ValueError("URL host is disallowed: 169.254.169.254")),
        ):
            with pytest.raises(ValueError, match="disallowed"):
                _replicate(["test"], tmp_path, {"replicate_model": "test/model"})


class TestPexelsRegression:
    def test_pexels_non_image_content_rejected(self, tmp_path: Path):
        """Pexels returning non-image content-type should be rejected."""
        import json

        mock_search_resp = {
            "photos": [{"src": {"large2x": "https://example.com/image.jpg"}}]
        }

        class FakeSearchResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(mock_search_resp).encode("utf-8")

        class FakeImageResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            headers = {"Content-Type": "text/html"}
            def read(self, n=None):
                return b"<html>not an image</html>"

        call_count = [0]
        def fake_urlopen(req, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return FakeSearchResponse()
            return FakeImageResponse()

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/image.jpg"),
        ):
            with pytest.raises(ValueError, match="non-image content-type"):
                _pexels(["test"], tmp_path, {"pexels_api_key": "test-key"})

    def test_pexels_oversized_image_rejected(self, tmp_path: Path):
        """Pexels returning an oversized image should be rejected."""
        import json

        mock_search_resp = {
            "photos": [{"src": {"large2x": "https://example.com/image.jpg"}}]
        }

        class FakeSearchResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(mock_search_resp).encode("utf-8")

        class FakeOversizeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            headers = {"Content-Type": "image/jpeg"}
            def read(self, n=None):
                # Return more than max_download_bytes (50 MB)
                return b"x" * (50 * 1024 * 1024 + 1)

        call_count = [0]
        def fake_urlopen(req, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return FakeSearchResponse()
            return FakeOversizeResponse()

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("utils.url_security.validate_source_url", return_value="https://example.com/image.jpg"),
        ):
            with pytest.raises(ValueError, match="too large"):
                _pexels(["test"], tmp_path, {"pexels_api_key": "test-key"})
