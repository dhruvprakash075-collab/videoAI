import json
from pathlib import Path
from unittest.mock import patch

from video.image_gen.qwen_repose import (
    _patch_qwen_workflow,
    build_qwen_edit_prompt,
    preflight_qwen_edit,
    repose_character,
    validate_qwen_workflow_template,
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


def test_validate_qwen_workflow_template_accepts_committed_template():
    workflow = Path("config/comfyui/workflows/qwen_image_edit_api.json")

    issues = validate_qwen_workflow_template(workflow)

    assert issues == []


def test_validate_qwen_workflow_template_reports_missing_placeholders(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"1": {"inputs": {"image": "__BASE_IMAGE__"}}}), encoding="utf-8")

    issues = validate_qwen_workflow_template(workflow)

    assert any("missing required placeholder" in issue for issue in issues)
    assert any("__EDIT_PROMPT__" in issue for issue in issues)


def test_patch_qwen_workflow_coerces_runtime_values(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(
        json.dumps(
            {
                "1": {
                    "inputs": {
                        "model_path": "__MODEL_PATH__",
                        "backend": "placeholder-backend",
                        "vram_offload": False,
                    },
                    "class_type": "QwenImageEdit2509Loader",
                },
                "2": {
                    "inputs": {
                        "seed": "__SEED__",
                        "steps": "__STEPS__",
                        "cfg": "__CFG__",
                        "denoise": "__DENOISE__",
                        "filename_prefix": "__FILENAME_PREFIX__",
                    },
                    "class_type": "QwenImageEditSampler",
                },
                "3": {
                    "inputs": {"text": "__EDIT_PROMPT__"},
                    "class_type": "QwenImageEditTextEncode",
                    "_meta": {"title": "Edit prompt"},
                },
            }
        ),
        encoding="utf-8",
    )
    config = {
        "image_gen": {
            "qwen_edit": {
                "backend": "nunchaku",
                "model_path": "models/qwen.safetensors",
                "steps": 8,
                "cfg": 1.25,
                "denoise": 0.55,
                "vram_offload": True,
            }
        }
    }

    patched = _patch_qwen_workflow(
        workflow,
        base_image_path=tmp_path / "base.png",
        character_image_path=tmp_path / "character.png",
        edit_prompt="place hero in scene",
        output_path=tmp_path / "scene_01.png",
        seed=123,
        config=config,
    )

    loader_inputs = patched["1"]["inputs"]
    sampler_inputs = patched["2"]["inputs"]
    prompt_inputs = patched["3"]["inputs"]
    assert loader_inputs["model_path"] == "models/qwen.safetensors"
    assert loader_inputs["backend"] == "nunchaku"
    assert loader_inputs["vram_offload"] is True
    assert sampler_inputs["seed"] == 123
    assert sampler_inputs["steps"] == 8
    assert sampler_inputs["cfg"] == 1.25
    assert sampler_inputs["denoise"] == 0.55
    assert sampler_inputs["filename_prefix"] == "scene_01"
    assert prompt_inputs["text"] == "place hero in scene"


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
    with (
        patch.object(image_gen, "_qwen_preflight_issues", return_value=[]),
        patch.object(image_gen, "_comfyui_qwen_edit", return_value=[]) as qwen,
    ):
        image_gen.generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    qwen.assert_called_once()
