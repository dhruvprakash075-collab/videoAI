from pathlib import Path
from unittest.mock import patch

from video.image_gen.qwen_repose import (
    build_qwen_edit_prompt,
    preflight_qwen_edit,
    repose_character,
)


def test_build_qwen_edit_prompt_includes_depth_and_props():
    prompt = build_qwen_edit_prompt(
        {"visual_description": "young hero with black hair and a gray coat"},
        "standing in front of a tree, holding a lantern",
    )

    assert "complete finished scene" in prompt
    assert "in front of an object" in prompt
    assert "holding" in prompt
    assert "young hero" in prompt
    assert "lantern" in prompt


def test_preflight_qwen_edit_reports_missing_model(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text("{}", encoding="utf-8")
    config = {
        "image_gen": {
            "qwen_edit": {
                "workflow_path": str(workflow),
                "model_path": "",
            }
        }
    }

    missing = preflight_qwen_edit(config)

    assert any("model_path is empty" in item for item in missing)


def test_repose_character_falls_back_to_base_when_preflight_fails(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"fake-png")
    out = tmp_path / "scene_01.png"
    config = {"image_gen": {"qwen_edit": {"enabled": True, "model_path": ""}}}

    result = repose_character(str(base), "hero", "place hero in scene", str(out), config, "project")

    assert result == str(base)
    assert base.read_bytes() == b"fake-png"


def test_generate_images_qwen_mode_dispatches_two_pass(tmp_path: Path):
    from video.image_gen import image_gen

    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "composition_mode": "qwen_edit",
            "qwen_edit": {"enabled": True},
        }
    }
    with patch.object(image_gen, "_comfyui_qwen_edit", return_value=[]) as qwen:
        image_gen.generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    qwen.assert_called_once()
