"""Tests for the 2026-06-02 operator-preferences sweep:

- visual.num_scenes default 6 → 4
- script.default_images_per_segment default 6 → 4
- script.max_images_per_segment default 10 → 8
- tts.omnivoice.speed default 0.85 → 0.5
- image_gen.preview_steps default 8 → 12 (match production, no dry-run downgrade)
- subtitles.language default → "en" (force English-only subtitle text)
- narrator.include_character_descriptions default → false (no visual desc in narration)
- build_segment_prompt honors include_character_descriptions flag
- visual.style emphasizes "semi-realistic, Arcane-style influenced"
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestOperatorPreferences:
    def test_visual_num_scenes_default_4(self):
        cfg = _load_config()
        assert cfg["visual"]["num_scenes"] == 4, (
            f"visual.num_scenes must be 4 (operator preference, 2026-06-02). "
            f"Got: {cfg['visual']['num_scenes']}"
        )

    def test_default_images_per_segment_4(self):
        cfg = _load_config()
        assert cfg["script"]["default_images_per_segment"] == 4, (
            f"script.default_images_per_segment must be 4 (4 slides default). "
            f"Got: {cfg['script']['default_images_per_segment']}"
        )

    def test_max_images_per_segment_8(self):
        cfg = _load_config()
        assert cfg["script"]["max_images_per_segment"] == 8, (
            f"script.max_images_per_segment must be 8 (proportional cap). "
            f"Got: {cfg['script']['max_images_per_segment']}"
        )

    def test_voice_speed_half(self):
        cfg = _load_config()
        assert cfg["tts"]["omnivoice"]["speed"] == 0.5, (
            f"tts.omnivoice.speed must be 0.5 (decreased per operator request). "
            f"Got: {cfg['tts']['omnivoice']['speed']}"
        )

    def test_preview_steps_matches_production(self):
        cfg = _load_config()
        assert cfg["image_gen"]["preview_steps"] == cfg["image_gen"]["steps"] == 12, (
            f"image_gen.preview_steps must equal steps (12) so dry-run quality "
            f"matches production. Got preview_steps={cfg['image_gen']['preview_steps']}, "
            f"steps={cfg['image_gen']['steps']}"
        )

    def test_subtitles_language_english(self):
        cfg = _load_config()
        assert cfg["subtitles"]["language"] == "en", (
            f"subtitles.language must be 'en' (force English-only SRT). "
            f"Got: {cfg['subtitles']['language']}"
        )

    def test_visual_style_arcane_semi_realistic(self):
        cfg = _load_config()
        style = cfg["visual"]["style"].lower()
        assert "arcane" in style, (
            f"visual.style must reference Arcane. Got: {cfg['visual']['style']!r}"
        )
        assert "semi-realistic" in style, (
            f"visual.style must include 'semi-realistic'. Got: {cfg['visual']['style']!r}"
        )

    def test_narrator_include_character_descriptions_false(self):
        cfg = _load_config()
        assert cfg["narrator"]["include_character_descriptions"] is False, (
            f"narrator.include_character_descriptions must be false (suppress "
            f"in-narration visual descriptions). Got: "
            f"{cfg['narrator']['include_character_descriptions']}"
        )


class TestBuildSegmentPrompt:
    def test_include_character_descriptions_true(self):
        from utils.story_planner import build_segment_prompt

        plan = {"seg": 1, "title": "T", "summary": "S", "key_event": "K", "mood": "epic"}
        prompt = build_segment_prompt(plan, "ctx", 5, 200, include_character_descriptions=True)
        assert "anchor their visual identity" in prompt
        assert "Do NOT insert character visual descriptions" not in prompt

    def test_include_character_descriptions_false(self):
        from utils.story_planner import build_segment_prompt

        plan = {"seg": 1, "title": "T", "summary": "S", "key_event": "K", "mood": "epic"}
        prompt = build_segment_prompt(plan, "ctx", 5, 200, include_character_descriptions=False)
        assert "Do NOT insert character visual descriptions" in prompt
        assert "anchor their visual identity" not in prompt

    def test_default_is_false(self):
        from utils.story_planner import build_segment_prompt

        plan = {"seg": 1, "title": "T", "summary": "S", "key_event": "K", "mood": "epic"}
        prompt = build_segment_prompt(plan, "ctx", 5, 200)
        assert "Do NOT insert character visual descriptions" in prompt


class TestWriteSrtSubtitleLanguage:
    def test_subtitle_language_default_auto(self):
        import inspect

        from video.renderer.assembler import _write_srt

        sig = inspect.signature(_write_srt)
        assert sig.parameters["subtitle_language"].default == "auto", (
            f"_write_srt subtitle_language default must be 'auto' (preserves "
            f"old behavior for tests/direct API calls; production reads 'en' "
            f"from config). Got: {sig.parameters['subtitle_language'].default!r}"
        )

    def test_production_path_passes_subtitle_language_from_config(self):
        """Verify assembler.py contains the subtitle language config read.

        Uses direct file reading instead of inspect.getsource because
        inspect can return the wrong source on CRLF files (Windows issue
        with line-offset calculation in the inspect module).
        """
        import video.renderer.assembler as _asm_mod

        src_path = Path(_asm_mod.__file__)
        src = src_path.read_text(encoding="utf-8")
        assert 'sub_cfg.get("language"' in src, (
            "assembler.py must read subtitle language from config "
            "(sub_cfg.get('language', ...)) and pass it to _write_srt"
        )
