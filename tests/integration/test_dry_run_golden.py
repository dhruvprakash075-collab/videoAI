"""C4.2 — Golden dry-run snapshot integration test.

Runs the actual pipeline in dry-run mode, intercepts LLM calls, and verifies
the generated files (manifest and chapters) for correctness.
"""

import json
from pathlib import Path
from unittest.mock import patch

from core.pipeline_long import run_long_pipeline


def test_dry_run_golden_snapshot(tmp_path, monkeypatch):
    # Change CWD to tmp_path to isolate all file outputs
    monkeypatch.chdir(tmp_path)

    # Base configuration overlay
    cfg = {
        "video": {
            "total_duration_min": 1,
            "segment_duration_min": 1,
            "resolution": "1920x1080",
            "fps": 24,
            "output_path": "studio_outputs/test_golden_dry_run_final_video.mp4",
        },
        "script": {
            "default_images_per_segment": 2,
            "max_images_per_segment": 5,
            "words_per_segment": 130,
        },
        "memory": {"memory_file": "studio_checkpoints/story_memory.json"},
        "checkpoint": {"dir": "studio_checkpoints"},
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
        },
        "tts": {
            "engine": "omnivoice",
            "lang": "hi",
        },
        "image_gen": {
            "sd_model_path": "DreamShaper_8.safetensors",
            "steps": 20,
            "width": 1024,
            "height": 1024,
        },
        "characters": {
            "protagonist": {
                "name": "The Protagonist",
                "description": "young adult",
                "keywords": ["hero"],
            }
        },
    }

    mock_outline = [
        {"seg": 1, "title": "Introductory Scene", "num_images": 2, "char_presence": [{}, {}]},
        {"seg": 2, "title": "Climactic Confrontation", "num_images": 2, "char_presence": [{}, {}]},
    ]

    with (
        patch("core.pipeline_long.run_pre_production", return_value={}) as mock_pre_prod,
        patch("core.pipeline_long.run_preflight_checks") as mock_preflight,
        patch("core.pipeline_long.plan_outline", return_value=mock_outline) as mock_plan_outline,
        patch("core.main.create_writer") as mock_create_writer,
        patch("core.main.create_director") as mock_create_director,
        patch("audio.audio_proxy.normalize_tts_engine", return_value="omnivoice"),
        patch("utils.load_config", return_value=cfg),
    ):
        res = run_long_pipeline(
            topic="test_golden_dry_run",
            resume=False,
            dry_run=True,
            fast_dry_run=True,
        )

    # 1. Assert result dict structure
    assert res["status"] == "dry_run"
    assert res["segments"] == 2
    assert "test_golden_dry_run_final_video.mp4" in res["output"]

    # 2. Verify files created on disk
    manifest_dir = Path("studio_outputs/test_golden_dry_run")
    manifest_path = manifest_dir / "run_manifest.json"
    chapters_path = manifest_dir / "chapters.txt"
    final_chapters_path = Path("studio_outputs/test_golden_dry_run_final_video_chapters.txt")

    assert manifest_path.exists(), "Manifest file was not created!"
    assert chapters_path.exists(), "Chapters file was not created!"
    assert final_chapters_path.exists(), "Chapters copy file was not created!"

    # 3. Assert manifest contents
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_data["topic"] == "test_golden_dry_run"
    assert manifest_data["status"] == "dry_run"
    assert manifest_data["segments_completed"] == 2
    assert "test_golden_dry_run_final_video.mp4" in manifest_data["final_video"]
    assert manifest_data["models"]["director"] == "hermes-director"
    assert manifest_data["models"]["writer"] == "zephyr-writer"
    assert manifest_data["settings"]["resolution"] == "1920x1080"
    assert manifest_data["settings"]["fps"] == 24

    # 4. Assert chapters content
    chapters_lines = chapters_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(chapters_lines) == 2
    assert "00:00 Introductory Scene" in chapters_lines[0]
    assert "00:30 Climactic Confrontation" in chapters_lines[1]

    final_chapters_lines = final_chapters_path.read_text(encoding="utf-8").strip().splitlines()
    assert final_chapters_lines == chapters_lines
