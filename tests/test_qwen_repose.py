import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from video.image_gen.qwen_repose import (
    QwenEditResult,
    _cache_path,
    _comfyui_image_ref,
    _patch_qwen_workflow,
    build_qwen_edit_prompt,
    preflight_qwen_edit,
    repose_character_detailed,
    validate_qwen_workflow_template,
)


def _workflow_with_required_placeholders() -> dict:
    return {
        "1": {
            "inputs": {
                "image": "__BASE_IMAGE__",
                "reference": "__CHARACTER_IMAGE__",
                "text": "__EDIT_PROMPT__",
                "filename_prefix": "__FILENAME_PREFIX__",
                "model_path": "__MODEL_PATH__",
                "seed": "__SEED__",
                "steps": "__STEPS__",
                "cfg": "__CFG__",
                "denoise": "__DENOISE__",
            },
            "class_type": "QwenImageEditSampler",
        }
    }


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


def test_preflight_qwen_edit_reports_missing_workflow_and_custom_node(tmp_path: Path):
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"fake-model")
    config = {
        "image_gen": {
            "comfyui": {"root": str(tmp_path / "ComfyUI")},
            "qwen_edit": {
                "workflow_path": str(tmp_path / "missing_workflow.json"),
                "model_path": str(model),
                "required_custom_nodes": ["ComfyUI-nunchaku"],
            },
        }
    }

    missing = preflight_qwen_edit(config)

    assert any("workflow file not found" in item for item in missing)
    assert any("missing ComfyUI custom node: ComfyUI-nunchaku" in item for item in missing)
    assert not any("model_path" in item for item in missing)


def test_preflight_qwen_edit_reports_missing_lightning_lora(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    data = _workflow_with_required_placeholders()
    data["1"]["inputs"]["lightning_lora"] = "__LIGHTNING_LORA__"
    workflow.write_text(json.dumps(data), encoding="utf-8")
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"fake-model")
    config = {
        "image_gen": {
            "qwen_edit": {
                "workflow_path": str(workflow),
                "model_path": str(model),
                "lightning_lora": str(tmp_path / "missing_lora.safetensors"),
            }
        }
    }

    missing = preflight_qwen_edit(config)

    assert missing == [f"qwen_edit lightning_lora not found: {tmp_path / 'missing_lora.safetensors'}"]


def test_validate_qwen_workflow_template_accepts_committed_template():
    workflow = Path("config/comfyui/workflows/qwen_image_edit_api.json")

    issues = validate_qwen_workflow_template(workflow)

    assert issues == []


def test_committed_workflow_has_resolution_steps():
    """Phase 1: ImageScaleToTotalPixels must include resolution_steps for current ComfyUI."""
    workflow = json.loads(
        Path("config/comfyui/workflows/qwen_image_edit_api.json").read_text(encoding="utf-8")
    )
    scale_node = workflow["3"]
    assert scale_node["class_type"] == "ImageScaleToTotalPixels"
    assert scale_node["inputs"]["resolution_steps"] == 1


def test_committed_workflow_has_hardware_safe_loader():
    """Phase 1: NunchakuQwenImageDiTLoader must use 1 GPU block for 6 GB VRAM."""
    workflow = json.loads(
        Path("config/comfyui/workflows/qwen_image_edit_api.json").read_text(encoding="utf-8")
    )
    loader = workflow["6"]
    assert loader["class_type"] == "NunchakuQwenImageDiTLoader"
    assert loader["inputs"]["num_blocks_on_gpu"] == 1
    assert loader["inputs"]["cpu_offload"] == "enable"
    assert loader["inputs"]["use_pin_memory"] == "disable"


def test_validate_qwen_workflow_template_reports_invalid_json(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text("{not-json", encoding="utf-8")

    issues = validate_qwen_workflow_template(workflow)

    assert len(issues) == 1
    assert "workflow JSON is invalid" in issues[0]


def test_validate_qwen_workflow_template_reports_missing_placeholders(tmp_path: Path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"1": {"inputs": {"image": "__BASE_IMAGE__"}}}), encoding="utf-8")

    issues = validate_qwen_workflow_template(workflow)

    assert any("missing required placeholder" in issue for issue in issues)
    assert any("__EDIT_PROMPT__" in issue for issue in issues)


def test_comfyui_image_ref_joins_subfolder():
    assert _comfyui_image_ref({"name": "a.png", "subfolder": "", "type": "input"}) == "a.png"
    assert _comfyui_image_ref({"name": "b.png", "subfolder": "chars", "type": "input"}) == "chars/b.png"


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
        base_image_ref="base.png",
        character_image_ref="character.png",
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


def test_patch_qwen_workflow_stages_loadimage_refs_on_committed_template():
    workflow = Path("config/comfyui/workflows/qwen_image_edit_api.json")
    config = {"image_gen": {"qwen_edit": {"steps": 8, "cfg": 1.0, "denoise": 0.6}}}

    patched = _patch_qwen_workflow(
        workflow,
        base_image_ref="bg.png",
        character_image_ref="hero.png",
        edit_prompt="place hero in scene",
        output_path=Path("scene_07.png"),
        seed=42,
        config=config,
    )

    # LoadImage nodes receive the uploaded refs, not host filesystem paths.
    assert patched["1"]["inputs"]["image"] == "bg.png"
    assert patched["2"]["inputs"]["image"] == "hero.png"
    # The downstream scale node keeps its node link untouched (not clobbered).
    assert patched["3"]["inputs"]["image"] == ["1", 0]
    # Only the positive encoder gets the edit prompt; the negative stays empty.
    assert patched["10"]["inputs"]["prompt"] == "place hero in scene"
    assert patched["9"]["inputs"]["prompt"] == ""
    # The sampler is coerced to deterministic numeric values.
    assert patched["12"]["inputs"]["seed"] == 42
    assert patched["12"]["inputs"]["steps"] == 8
    assert patched["14"]["inputs"]["filename_prefix"] == "scene_07"


def test_qwen_cache_key_changes_with_seed_model_and_lora(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"base-png")
    config = {
        "image_gen": {
            "qwen_edit": {
                "backend": "nunchaku",
                "model_path": "models/qwen-a.safetensors",
                "lightning_lora": "",
                "steps": 8,
                "denoise": 0.6,
            }
        }
    }

    original = _cache_path(base, "identity-a", "place hero", 1, config)
    different_seed = _cache_path(base, "identity-a", "place hero", 2, config)
    config["image_gen"]["qwen_edit"]["model_path"] = "models/qwen-b.safetensors"
    different_model = _cache_path(base, "identity-a", "place hero", 1, config)
    config["image_gen"]["qwen_edit"]["lightning_lora"] = "models/lightning.safetensors"
    different_lora = _cache_path(base, "identity-a", "place hero", 1, config)

    assert different_seed != original
    assert different_model != original
    assert different_lora != different_model


def test_repose_character_uses_cache_without_comfyui(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"base-png")
    output = tmp_path / "out.png"
    cached = tmp_path / ".qwen_edit_cache" / "cached.png"
    cached.parent.mkdir()
    cached.write_bytes(b"cached-png")
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps(_workflow_with_required_placeholders()), encoding="utf-8")
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"fake-model")
    reference = tmp_path / "character.png"
    reference.write_bytes(b"reference-png")
    config = {
        "image_gen": {
            "qwen_edit": {
                "enabled": True,
                "workflow_path": str(workflow),
                "model_path": str(model),
            }
        }
    }

    with (
        patch(
            "video.image_gen.qwen_repose._ensure_reference_image",
            return_value=(reference, "identity-a", {"description": "hero"}),
        ),
        patch("video.image_gen.qwen_repose._cache_path", return_value=cached),
        patch("video.image_gen.comfyui_client.ComfyUIClient") as comfy_client,
    ):
        result = repose_character_detailed(str(base), "hero", "place hero", str(output), config, "project").output_path

    assert result == str(output)
    assert output.read_bytes() == b"cached-png"
    comfy_client.assert_not_called()


def test_repose_character_falls_back_to_base_when_preflight_fails(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"fake-png")
    out = tmp_path / "scene_01.png"
    config = {"image_gen": {"qwen_edit": {"enabled": True, "model_path": ""}}}

    result = repose_character_detailed(str(base), "hero", "place hero in scene", str(out), config, "project").output_path

    assert result == str(base)
    assert base.read_bytes() == b"fake-png"


def _staged_qwen_config(tmp_path: Path) -> dict:
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps(_workflow_with_required_placeholders()), encoding="utf-8")
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"fake-model")
    return {
        "image_gen": {
            "qwen_edit": {
                "enabled": True,
                "workflow_path": str(workflow),
                "model_path": str(model),
            }
        }
    }


def test_repose_character_detailed_uploads_inputs_and_reports_edited(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"bg")
    reference = tmp_path / "hero.png"
    reference.write_bytes(b"ref")
    config = _staged_qwen_config(tmp_path)
    cache = tmp_path / ".qwen_edit_cache" / "frame.png"  # does not exist

    generated = tmp_path / "generated.png"
    generated.write_bytes(b"composited")

    fake_client = MagicMock()
    fake_client.upload_image.side_effect = [
        {"name": "scene_01.png", "subfolder": "", "type": "input"},
        {"name": "hero.png", "subfolder": "refs", "type": "input"},
    ]
    fake_client.generate_image.return_value = [generated]

    fake_runtime = MagicMock()
    fake_runtime.ensure_running.return_value = True
    fake_runtime.base_url = "http://127.0.0.1:8188"

    with (
        patch(
            "video.image_gen.qwen_repose._ensure_reference_image",
            return_value=(reference, "identity-a", {"description": "hero"}),
        ),
        patch("video.image_gen.qwen_repose._cache_path", return_value=cache),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=fake_client),
        patch(
            "video.image_gen.comfyui_runtime.get_comfyui_runtime",
            return_value=fake_runtime,
        ),
    ):
        result = repose_character_detailed(
            str(base), "hero", "place hero", str(base), config, "project"
        )

    assert result.status == "edited"
    assert result.composited is True
    # Both the background and the character reference are staged into ComfyUI.
    assert fake_client.upload_image.call_count == 2
    # The patched workflow used the uploaded refs (subfolder joined for char).
    workflow_arg = fake_client.generate_image.call_args[0][0]
    assert workflow_arg["1"]["inputs"]["image"] == "scene_01.png"
    assert workflow_arg["1"]["inputs"]["reference"] == "refs/hero.png"
    # The frame on disk was overwritten with the composited output.
    assert base.read_bytes() == b"composited"


def test_repose_character_detailed_reports_failed_when_no_image(tmp_path: Path):
    base = tmp_path / "scene_01.png"
    base.write_bytes(b"bg")
    reference = tmp_path / "hero.png"
    reference.write_bytes(b"ref")
    config = _staged_qwen_config(tmp_path)
    cache = tmp_path / ".qwen_edit_cache" / "frame.png"

    fake_client = MagicMock()
    fake_client.upload_image.return_value = {"name": "x.png", "subfolder": "", "type": "input"}
    fake_client.generate_image.return_value = []  # ComfyUI produced nothing

    fake_runtime = MagicMock()
    fake_runtime.ensure_running.return_value = True
    fake_runtime.base_url = "http://127.0.0.1:8188"

    with (
        patch(
            "video.image_gen.qwen_repose._ensure_reference_image",
            return_value=(reference, "identity-a", {"description": "hero"}),
        ),
        patch("video.image_gen.qwen_repose._cache_path", return_value=cache),
        patch("video.image_gen.comfyui_client.ComfyUIClient", return_value=fake_client),
        patch(
            "video.image_gen.comfyui_runtime.get_comfyui_runtime",
            return_value=fake_runtime,
        ),
    ):
        result = repose_character_detailed(
            str(base), "hero", "place hero", str(base), config, "project"
        )

    assert result.status == "failed"
    assert result.composited is False
    assert result.output_path == str(base)
    assert base.read_bytes() == b"bg"  # frame untouched


def test_comfyui_qwen_edit_records_degradation_without_rerouting(tmp_path: Path):
    from video.image_gen import image_gen

    frames = [tmp_path / "scene_01.png", tmp_path / "scene_02.png"]
    for f in frames:
        f.write_bytes(b"bg")

    cfg = {
        "backend": "comfyui",
        "composition_mode": "qwen_edit",
        "qwen_edit": {"enabled": True, "character_threshold": 0.05},
    }

    def fake_detailed(base, char, prompt, out, config, project_id, *, seed=0):
        return QwenEditResult("failed", base, "comfyui down")

    with (
        patch.object(image_gen, "_comfyui", return_value=frames),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch(
            "video.image_gen.qwen_repose.repose_character_detailed",
            side_effect=fake_detailed,
        ),
        patch.object(image_gen, "_log_qwen_degradation") as log_degradation,
    ):
        result = image_gen._comfyui_qwen_edit(
            ["a", "b"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.9}, {"hero": 0.9}],
            project_id="p",
        )

    # All frames are returned (kept their backgrounds); nothing rerouted to Bonsai.
    assert [str(p) for p in result] == [str(f) for f in frames]
    # Each non-composited frame recorded a qwen_edit_fallback degradation.
    assert log_degradation.call_count == 2


def test_comfyui_qwen_edit_skips_frames_below_threshold(tmp_path: Path):
    from video.image_gen import image_gen

    frames = [tmp_path / "scene_01.png", tmp_path / "scene_02.png"]
    for f in frames:
        f.write_bytes(b"bg")

    cfg = {
        "backend": "comfyui",
        "composition_mode": "qwen_edit",
        "qwen_edit": {"enabled": True, "character_threshold": 0.05},
    }

    seen_chars = []

    def fake_detailed(base, char, prompt, out, config, project_id, *, seed=0):
        seen_chars.append(char)
        return QwenEditResult("edited", out, "")

    with (
        patch.object(image_gen, "_comfyui", return_value=frames),
        patch.object(image_gen, "_free_comfyui_memory"),
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch(
            "video.image_gen.qwen_repose.repose_character_detailed",
            side_effect=fake_detailed,
        ),
        patch.object(image_gen, "_log_qwen_degradation") as log_degradation,
    ):
        result = image_gen._comfyui_qwen_edit(
            ["a", "b"],
            tmp_path,
            cfg,
            char_presence=[{"hero": 0.9}, {"hero": 0.01}],
            project_id="p",
        )

    # Only the above-threshold frame is sent to Qwen; the other keeps its bg.
    assert seen_chars == ["hero"]
    assert log_degradation.call_count == 0
    assert len(result) == 2


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
        patch.object(image_gen, "_qwen_resource_issues", return_value=[]),
        patch.object(image_gen, "_comfyui_qwen_edit", return_value=[]) as qwen,
    ):
        image_gen.generate_images(["forest"], tmp_path, cfg, char_presence=[{"hero": 0.1}], project_id="p")

    qwen.assert_called_once()
