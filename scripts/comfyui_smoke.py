"""Real-instance ComfyUI smoke gate (AGENTS.md).

Runs the starter workflow (config/comfyui/workflows/video_ai_text_to_image.json)
end-to-end on a real ComfyUI instance and asserts frames are saved.

The stub-based test suite cannot catch executor-level drift (the stub model
never exercises comfy_api's parse_class_inputs) — only this gate can.

Usage:
    venv\\Scripts\\python.exe scripts\\comfyui_smoke.py
"""
from __future__ import annotations

import json
import random
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

WORKFLOW = REPO_ROOT / "config" / "comfyui" / "workflows" / "video_ai_text_to_image.json"
FRAMES_DIR = REPO_ROOT / "external" / "comfyui" / "studio_outputs" / "frames"


def main() -> int:
    from config.config import load_config
    from video.image_gen.comfyui_runtime import ComfyUIRuntime

    runtime = ComfyUIRuntime(load_config())
    assert runtime.ensure_running(timeout=120.0), f"server not up at {runtime.base_url}"

    workflow = json.loads(WORKFLOW.read_text())
    # Vary the KSampler seed so the run isn't served from ComfyUI's execution
    # cache (a cached prompt never exercises model load/sampling/save).
    workflow["6"]["inputs"]["seed"] = random.randint(0, 2**31)
    started = time.time()
    req = urllib.request.Request(
        f"{runtime.base_url}/prompt",
        data=json.dumps({"prompt": workflow, "client_id": "smoke-gate"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        prompt_id = json.loads(resp.read())["prompt_id"]
    print(f"submitted: {prompt_id}")

    deadline = time.time() + 600
    while time.time() < deadline:
        with urllib.request.urlopen(f"{runtime.base_url}/history/{prompt_id}", timeout=10) as resp:
            hist = json.loads(resp.read())
        if prompt_id not in hist:
            time.sleep(2)
            continue
        status = hist[prompt_id]["status"]
        if status.get("completed") is True or status.get("status_str") == "success":
            break
        if status.get("status_str") in ("error", "failed"):
            print(f"FAILED: {json.dumps(status, default=str)[:2000]}")
            return 1
        time.sleep(2)
    else:
        print("TIMEOUT waiting for prompt completion")
        return 1

    # The saver overwrites scene_01.png in place, so detect the new frame by mtime.
    frames = sorted(
        (f for f in FRAMES_DIR.glob("*.png") if f.stat().st_mtime >= started - 1),
        key=lambda f: f.stat().st_mtime,
    )
    assert frames, f"no frames written to {FRAMES_DIR}"
    for f in frames[-3:]:
        print(f"  {f.name} {f.stat().st_size} bytes")
    print(f"SMOKE OK ({len(frames)} new frame(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
