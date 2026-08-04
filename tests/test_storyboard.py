"""Tests for core/storyboard.py — the pre-production storyboard approval gate.

Mocks the director agent (LLM + consultation), image generation, and panel
composition so no real Ollama/ComfyUI is needed. StoryStore writes go to a
temp root to avoid polluting studio_projects/.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core import storyboard as sb
from memory.project_store import StoryStore


@pytest.fixture
def tmp_root():
    """Temp dir via mkdtemp — avoids the pytest-asyncio tmp_path wrapper that
    fails on Windows with PermissionError (WinError 5) on the pytest-of-* dir."""
    d = Path(tempfile.mkdtemp())
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


class _FakeLlm:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0
        self.prompts = []

    def _call_ollama(self, prompt, format_json=False, seed=None):
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


class _FakeDirector:
    def __init__(self, llm_response: str, consult_choices=None):
        self.llm = _FakeLlm(llm_response)
        self._prompts = {
            "storyboard_plan": "Produce exactly {panel_count} panels. {outline} {characters} {style}"
        }
        self._consult_choices = list(consult_choices or ["Approve"])
        self.consult_calls = 0

    def _prompt(self, key, **kwargs):
        # Mirrors PromptsMixin._prompt: returns the raw template when no
        # kwargs are supplied (the caller formats it), or formats when given.
        template = self._prompts.get(key, "")
        if not kwargs:
            return template
        return template.format(**kwargs)

    def consult_user(self, question, options=None, allow_custom=True):
        self.consult_calls += 1
        return self._consult_choices.pop(0) if self._consult_choices else "Approve"


def _config(tmp_root, **overrides):
    cfg = {
        "storyboard": {
            "enabled": True,
            "panel_count": 2,
            "aspect": "16:9",
            "approval_retries": 1,
            "reuse_existing": True,
            "inject_shot_metadata": True,
        },
        "visual": {"style": "anime"},
        "characters": {
            "hero": {"name": "Hero", "description": "young hero, black hair"},
        },
    }
    cfg["storyboard"].update(overrides)
    return cfg


def _outline():
    return [
        {"seg": 1, "title": "Intro", "mood": "mysterious", "key_event": "Hero finds a map"},
        {"seg": 2, "title": "Climax", "mood": "action", "key_event": "Hero fights the villain"},
    ]


def _llm_json():
    return json.dumps(
        {
            "panels": [
                {
                    "beat": "Hero finds a map",
                    "shot_size": "wide",
                    "camera": "slow dolly-in",
                    "action": "Hero picks up the map",
                    "environment": "old library",
                    "dialogue": "",
                    "duration_sec": 5.0,
                },
                {
                    "beat": "Hero fights",
                    "shot_size": "close-up",
                    "camera": "whip pan",
                    "action": "Hero swings",
                    "environment": "rooftop",
                    "dialogue": "No!",
                    "duration_sec": 7.0,
                },
            ]
        }
    )


def _patch_generation(monkeypatch, tmp_root):
    """Patch generate_images + compose_panel_pages to return fake paths."""
    def _fake_generate(prompts, output_dir, config, char_presence=None, project_id=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, _p in enumerate(prompts):
            p = output_dir / f"panel_{i + 1}.png"
            p.write_bytes(b"fake")
            paths.append(p)
        return paths

    def _fake_compose(
        image_paths, output_dir, *, width=1920, height=1080, margin=48, gutter=24,
        border=6, prefix="manga_page", layout_file=None, fallback_layout_file=None,
        page_aspect=1.414, page_blur=True,
    ):
        output_dir.mkdir(parents=True, exist_ok=True)
        sheet = output_dir / f"{prefix}_1.png"
        sheet.write_bytes(b"sheet")
        return [sheet]

    monkeypatch.setattr("video.image_gen.image_gen.generate_images", _fake_generate)
    monkeypatch.setattr("video.image_gen.panel_compositor.compose_panel_pages", _fake_compose)


def test_reuse_skip(tmp_root, monkeypatch):
    """An approved storyboard in StoryStore is reused without calling the LLM."""
    _patch_generation(monkeypatch, tmp_root)
    store = StoryStore("topic", root=tmp_root)
    sheet = tmp_root / "x.png"
    sheet.write_bytes(b"sheet")
    store.save_storyboard({"status": "approved", "sheet_path": str(sheet), "panels": []})

    director = _FakeDirector(_llm_json())
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result["status"] == "approved"
    assert result["sheet_path"] == str(sheet)
    assert director.llm.calls == 0


def test_reuse_skips_when_sheet_missing(tmp_root, monkeypatch):
    """An approved record whose sheet file is gone is regenerated, not reused."""
    _patch_generation(monkeypatch, tmp_root)
    store = StoryStore("topic", root=tmp_root)
    store.save_storyboard({"status": "approved", "sheet_path": "missing.png", "panels": []})

    director = _FakeDirector(_llm_json())
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert director.llm.calls == 1
    assert result["status"] == "approved"


def test_force_storyboard_regenerates(tmp_root, monkeypatch):
    """--force-storyboard bypasses the reuse check."""
    _patch_generation(monkeypatch, tmp_root)
    store = StoryStore("topic", root=tmp_root)
    store.save_storyboard({"status": "approved", "sheet_path": "old.png", "panels": []})

    director = _FakeDirector(_llm_json())
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic",
        cli_flags={"force_storyboard": True}, root=tmp_root,
    )
    assert director.llm.calls == 1
    assert result["sheet_path"] != "old.png"


def test_panel_plan_parse(tmp_root):
    """LLM JSON is parsed into structured panels."""
    panels = sb._parse_panels(_llm_json(), 2)
    assert len(panels) == 2
    assert panels[0]["shot_size"] == "wide"
    assert panels[0]["duration_sec"] == 5.0
    assert panels[1]["dialogue"] == "No!"


def test_panel_parse_pads_short_response(tmp_root):
    """Under-delivered LLM responses are padded to panel_count."""
    panels = sb._parse_panels(json.dumps({"panels": [{"beat": "only"}]}), 3)
    assert len(panels) == 3
    assert panels[1]["shot_size"] == "medium"  # alternation continues


def test_store_roundtrip(tmp_root):
    """save_storyboard/get_storyboard roundtrip."""
    store = StoryStore("topic", root=tmp_root)
    assert store.get_storyboard() is None
    store.save_storyboard({"status": "approved", "sheet_path": "s.png", "panels": []})
    assert store.get_storyboard()["status"] == "approved"


def test_approval_flow_yes(tmp_root, monkeypatch):
    """--yes auto-approves (consult_user returns default 'Approve')."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json(), consult_choices=["Approve"])
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result["status"] == "approved"
    assert director.consult_calls == 1


def test_approval_proceed_default_approves(tmp_root, monkeypatch):
    """UI-mode default reply 'Proceed as planned.' approves instead of regenerating."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json(), consult_choices=["Proceed as planned."])
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result["status"] == "approved"
    assert director.llm.calls == 1  # no regen loop on the UI default reply


def test_garbage_llm_response_skips(tmp_root, monkeypatch):
    """An unparseable LLM response skips the storyboard instead of raising."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector("this is not json at all")
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result is None


def test_regenerate_retries_then_approves(tmp_root, monkeypatch):
    """Regenerate retries up to approval_retries, then auto-approves."""
    _patch_generation(monkeypatch, tmp_root)
    # 2 regenerations then approve; approval_retries=1 means max 2 attempts
    director = _FakeDirector(_llm_json(), consult_choices=["Regenerate", "Regenerate", "Approve"])
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result["status"] == "approved"
    assert director.llm.calls == 2  # attempt 1 + 1 retry


def test_config_gating_disabled(tmp_root, monkeypatch):
    """enabled: false skips storyboard entirely."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json())
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root, enabled=False), "topic", cli_flags={}, root=tmp_root
    )
    assert result is None
    assert director.llm.calls == 0


def test_no_storyboard_flag(tmp_root, monkeypatch):
    """--no-storyboard skips storyboard entirely."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json())
    result = sb.run_storyboard(
        director, _outline(), _config(tmp_root), "topic",
        cli_flags={"no_storyboard": True}, root=tmp_root,
    )
    assert result is None
    assert director.llm.calls == 0


def test_shot_metadata_injection(tmp_root):
    """enrich_prompts appends shot_metadata to the camera when enabled."""
    from utils.scene_director import enrich_prompts

    cfg = _config(tmp_root)
    plan = {"shot_metadata": "slow dolly-in, 8.5s"}
    enriched, _neg = enrich_prompts("hero in a room", "a mysterious scene", cfg, plan=plan)
    assert "slow dolly-in" in enriched
    assert "8.5s" in enriched


def test_shot_metadata_disabled(tmp_root):
    """inject_shot_metadata: false leaves the camera unchanged."""
    from utils.scene_director import enrich_prompts

    cfg = _config(tmp_root, inject_shot_metadata=False)
    # Use a token NOT in the base mysterious camera ("slow dolly-in" is).
    plan = {"shot_metadata": "whip pan, 8.5s"}
    enriched, _neg = enrich_prompts("hero in a room", "a mysterious scene", cfg, plan=plan)
    assert "whip pan" not in enriched
    assert "8.5s" not in enriched


def test_attach_shot_metadata(tmp_root):
    """Panels map onto outline segments (the per-segment plan dicts), round-robin."""
    from core.storyboard import attach_shot_metadata

    outline = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    panels = [
        {"camera": "slow dolly-in", "duration_sec": 8.0},
        {"camera": "whip pan", "duration_sec": 5.0},
    ]
    attach_shot_metadata(outline, panels)
    assert outline[0]["shot_metadata"] == "slow dolly-in, 8.0s"
    assert outline[1]["shot_metadata"] == "whip pan, 5.0s"
    assert outline[2]["shot_metadata"] == "slow dolly-in, 8.0s"  # wraps around
    assert "shot_metadata" not in {"title": "D"}


def test_attach_shot_metadata_no_panels(tmp_root):
    """No panels leaves outline segments untouched."""
    from core.storyboard import attach_shot_metadata

    outline = [{"title": "A"}]
    attach_shot_metadata(outline, [])
    assert "shot_metadata" not in outline[0]


def test_panel_count_dynamic_from_outline(tmp_root, monkeypatch):
    """Panel count derives from the outline (sum of num_images) when present."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json())
    outline = [
        {"seg": 1, "title": "A", "num_images": 3},
        {"seg": 2, "title": "B", "num_images": 2},
    ]
    result = sb.run_storyboard(
        director, outline, _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert result["status"] == "approved"
    assert "exactly 5 panels" in director.llm.prompts[0]
    assert len(result["panels"]) == 5


def test_panel_count_dynamic_capped(tmp_root, monkeypatch):
    """Derived panel count is capped at 12."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json())
    outline = [
        {"seg": 1, "title": "A", "num_images": 8},
        {"seg": 2, "title": "B", "num_images": 8},
    ]
    sb.run_storyboard(
        director, outline, _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert "exactly 12 panels" in director.llm.prompts[0]


def test_panel_count_falls_back_to_config(tmp_root, monkeypatch):
    """Without num_images in the outline, the config panel_count applies."""
    _patch_generation(monkeypatch, tmp_root)
    director = _FakeDirector(_llm_json())
    outline = [{"seg": 1, "title": "A"}, {"seg": 2, "title": "B"}]
    result = sb.run_storyboard(
        director, outline, _config(tmp_root), "topic", cli_flags={}, root=tmp_root
    )
    assert "exactly 2 panels" in director.llm.prompts[0]
    assert len(result["panels"]) == 2
