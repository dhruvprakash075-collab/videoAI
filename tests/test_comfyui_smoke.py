"""Smoke test - real ComfyUI generation."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: mark test as smoke test")


def pytest_collection_modifyitems(config, items):
    skip_smoke = pytest.mark.skip(reason="need --run-smoke option to run")
    for item in items:
        if "smoke" in item.keywords:
            if not config.getoption("--run-smoke", default=False):
                item.add_marker(skip_smoke)


@pytest.mark.smoke
def test_comfyui_runtime_is_running():
    """Check if ComfyUI server is accessible."""
    from video.image_gen.comfyui_runtime import ComfyUIRuntime
    from config.config import load_config

    config = load_config()
    runtime = ComfyUIRuntime(config)

    is_running = runtime.is_running(timeout=5.0)
    assert is_running, f"ComfyUI not running at {runtime.base_url}"

    print(f"ComfyUI is running at {runtime.base_url}")


@pytest.mark.smoke
def test_comfyui_generate_image():
    """Smoke test - generate real image through ComfyUI."""
    from pathlib import Path
    import tempfile
    from video.image_gen.image_gen import generate_images
    from config.config import load_config

    config = load_config()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)

        result = generate_images(
            "a simple test image",
            output_dir,
            config,
        )

        assert len(result) >= 1
        assert result[0].exists()
        assert result[0].suffix == ".png"

        import os
        size = os.path.getsize(result[0])
        assert size > 1000, f"Image too small: {size} bytes"

        print(f"Generated: {result[0]} ({size} bytes)")