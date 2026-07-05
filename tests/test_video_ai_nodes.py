"""Tests for the Video.AI ComfyUI V3 node suite."""

from __future__ import annotations

import importlib


def test_config_reader_pure_values(tmp_path):
    import yaml
    from video_ai_nodes.helpers import load_yaml, read_image_gen_values

    cfg = {
        "image_gen": {
            "backend": "comfyui",
            "guidance_scale": 3.5,
            "comfyui": {
                "width": 1344,
                "height": 768,
                "steps": 30,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "checkpoint": "DreamShaper_8.safetensors",
                "unload_after_batch": True,
            },
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    assert read_image_gen_values(load_yaml(path)) == (
        1344,
        768,
        30,
        7.0,
        "euler",
        "normal",
        "DreamShaper_8.safetensors",
        "",
        True,
    )


def test_package_imports_without_comfyui():
    mod = importlib.import_module("video_ai_nodes")
    assert hasattr(mod, "comfy_entrypoint")
