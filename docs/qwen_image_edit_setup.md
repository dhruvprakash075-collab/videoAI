# Qwen-Image-Edit two-pass setup

This feature uses Nunchaku INT4 and ships enabled behind live RAM/VRAM admission gates. When the machine has less than the configured headroom, it logs the reason and uses the normal one-pass ComfyUI path without attempting Qwen.

## What it does

1. **Pass 1:** ComfyUI + SD1.5 generates every background as a complete scene. No empty character hole is reserved.
2. **Pass 2:** Qwen-Image-Edit-2509 loads once and inserts the saved character into frames that have `char_presence` above the configured threshold.
3. Frames without a saved character stay exactly as the Pass 1 background.
4. Qwen handles character placement, depth/occlusion, and any prop the character is holding.

The output frame path is unchanged. Qwen overwrites/copies back to the same frame path so the Rust asset manifest and ffmpeg assembly can keep using the current image locations.

## Safety defaults

`main` must stay safe for normal renders:

```yaml
image_gen:
  composition_mode: qwen_edit
  qwen_edit:
    enabled: true
```

Do not commit a config change that enables Qwen by default. Enable it only in a local test config or a temporary working tree while running the GPU spike.

## Resource-gated normal-mode regression

Run these before the GPU spike to prove the merge did not change the default path:

```powershell
git checkout main
git pull origin main
```

Confirm Qwen is enabled with the calibrated resource gates:

```powershell
Select-String -Path config\config.yaml -Pattern "composition_mode: qwen_edit", "enabled: true", "min_available_ram_gib: 8.0", "min_free_vram_mib: 5000"
```

Run the focused tests:

```powershell
venv\Scripts\python.exe -m pytest tests/test_qwen_repose.py tests/test_image_gen.py tests/test_config_schemas.py tests/test_preflight.py tests/test_qwen_spike_check.py -q
```

Run the targeted Ruff check:

```powershell
venv\Scripts\ruff check video/image_gen/image_gen.py video/image_gen/qwen_repose.py utils/preflight.py config/config_schemas.py tests/test_qwen_repose.py tests/test_image_gen.py tests/test_config_schemas.py tests/test_preflight.py scripts/qwen_edit_spike_check.py tests/test_qwen_spike_check.py
```

Optional full local gate:

```powershell
venv\Scripts\python.exe -m pytest tests/ -q
venv\Scripts\ruff check .
```

## Repo-side local spike harness

Use the harness before the hardware run to keep the Qwen validation focused and repeatable:

```powershell
venv\Scripts\python.exe scripts\qwen_edit_spike_check.py --strict-defaults
```

The script checks that the committed config selects resource-gated Qwen, verifies the workflow template/model/custom-node paths, prints the focused pytest/Ruff commands, and writes a paste-ready Issue #23 result template to:

```text
.qwen_edit_cache/qwen_local_spike_results.md
```

When the model or custom node is absent, preflight falls back to one-pass. When live RAM or VRAM is below the configured threshold, admission also falls back before Qwen allocation.

## Recommended 6 GB setup

Use **Nunchaku INT4** first. It should use the least VRAM and avoid swapping the model for every frame because the implementation batches by model: all backgrounds first, then all Qwen edits.

Suggested config block:

```yaml
image_gen:
  composition_mode: qwen_edit
  qwen_edit:
    enabled: true
    backend: nunchaku
    workflow_path: config/comfyui/workflows/qwen_image_edit_api.json
    model_path: ""
    lightning_lora: ""
    steps: 8
    cfg: 1.0
    denoise: 0.6
    vram_offload: true
    min_available_ram_gib: 8.0
    min_free_vram_mib: 5000
    trigger: any_character
    character_threshold: 0.05
    cache_dir: .qwen_edit_cache
    timeout_seconds: 600
    poll_seconds: 1.0
    required_custom_nodes:
      - ComfyUI-nunchaku
```

The committed `model_path` points at the locally installed INT4 model. If it is missing, preflight keeps the one-pass result.

For YouTube 1080p framing, start with a 1024-class 16:9 generation size:

```yaml
image_gen:
  comfyui:
    width: 1024
    height: 576
```

The committed Qwen workflow independently scales its edit input to about one megapixel; there are no separate Qwen resolution keys in the strict config schema.

## Workflow template

`config/comfyui/workflows/qwen_image_edit_api.json` is a template with placeholders that the Python code patches before queueing the workflow:

- `__BASE_IMAGE__`
- `__CHARACTER_IMAGE__`
- `__EDIT_PROMPT__`
- `__FILENAME_PREFIX__`
- `__MODEL_PATH__`
- `__LIGHTNING_LORA__`
- `__SEED__`
- `__STEPS__`
- `__CFG__`
- `__DENOISE__`

If your installed Qwen/Nunchaku node pack uses different node class names, export a working ComfyUI API workflow and keep these placeholders in the relevant inputs.

## Preflight-gated dispatch

When `image_gen.backend: comfyui`, `image_gen.composition_mode: qwen_edit`, and `image_gen.qwen_edit.enabled: true`, the dispatcher runs Qwen preflight before entering the two-pass Qwen path.

Preflight checks only local readiness. It does not contact ComfyUI or run CUDA. It verifies:

- the Qwen workflow JSON exists and contains the required placeholders
- `image_gen.qwen_edit.model_path` is set and exists
- optional `lightning_lora` exists when configured
- configured ComfyUI custom nodes exist under the configured ComfyUI root

If preflight reports issues, the render logs them and uses normal one-pass ComfyUI instead of attempting Qwen. This keeps enabled-but-not-installed local configs from crashing or accidentally falling into a different generation path. If preflight passes but the Qwen runtime call fails later, the existing runtime fallback behavior still applies.

## Qwen cache behavior

Qwen edited frames are cached under the configured `image_gen.qwen_edit.cache_dir` relative to the frame output directory. The cache key includes:

- character identity hash
- base background image content hash
- full Qwen edit prompt
- Qwen seed
- Qwen backend
- Qwen steps and denoise values
- Qwen model path
- optional Lightning LoRA path

A cache hit copies the cached image back to the requested output frame path and skips the expensive ComfyUI Qwen call. Changing the seed, model path, LoRA path, background frame, character identity, or edit prompt should produce a different cache path.

## GPU spike protocol: RTX 4050 / 6 GB

Goal: prove the feature is usable on the target laptop GPU without changing normal-mode behavior.

### 1. Install and preflight

1. Install the Nunchaku / Qwen-Image-Edit-2509 ComfyUI node pack.
2. Place the INT4 model under the configured `model_path`.
3. Start ComfyUI once and confirm the workflow loads.
4. Run preflight with Qwen enabled in your local test config:

```powershell
venv\Scripts\python.exe bootstrap_pipeline.py --preflight-only
```

Expected:

- `qwen_edit` is skipped when disabled.
- `qwen_edit` reports `ok` after enabling and installing the workflow/model/nodes.
- Missing model/nodes should be `warn`, not a crash, because frames fall back to base images.

### 2. Measure VRAM during one frame

In one terminal, monitor VRAM:

```powershell
nvidia-smi --query-gpu=timestamp,memory.used,memory.free,utilization.gpu --format=csv -l 1
```

In another terminal, run a one-frame Qwen-enabled smoke test using a saved character project.

Record:

| Run | Resolution | Backend | Steps | Peak VRAM | Seconds/image | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1-frame | 1024x576 | Nunchaku INT4 | 8 | TBD | TBD | pass/fail |

Pass bar:

- No OOM on 6 GB.
- Qwen output writes back to the same frame path.
- If Qwen fails, base frame remains and render continues.

### 3. Measure three frames

Run a 3-frame project with one recurring saved character and at least three different instructions, for example:

1. character standing in front of a tree, holding a lantern
2. character sitting beside a fire, partially behind smoke
3. character walking through a doorway, one hand on the frame

Record:

| Frame | Character present? | Qwen used? | Seconds | Peak VRAM | Identity preserved? | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | yes | yes | TBD | TBD | yes/no |  |
| 2 | yes | yes | TBD | TBD | yes/no |  |
| 3 | yes | yes | TBD | TBD | yes/no |  |

Pass bar:

- SD1.5/ComfyUI backgrounds finish first.
- Qwen pass loads after the background pass.
- At least 3 character poses preserve recognizable identity.
- Held props are generated with the character, not pre-painted into the background.

### 4. Validate caching

Re-run the same 3-frame job without changing prompts, seed, model path, or character identity hash.

Expected:

- Qwen cache hits are logged.
- Expensive Qwen calls are skipped on the second run.
- Output frame paths remain unchanged.

### 5. Export real workflow if needed

The committed workflow is a template. If ComfyUI reports unknown class types or incompatible inputs:

1. Build a working graph manually in ComfyUI.
2. Export as API workflow JSON.
3. Replace only the Qwen workflow JSON.
4. Keep all placeholder names listed above in the correct inputs.
5. Re-run `tests/test_qwen_repose.py` and the preflight tests.

Do not touch `rust/**`, `video/renderer/**`, `audio/**`, `bootstrap_pipeline.py`, or the SQLite schema while fixing workflow-node mismatches.

## Smoke test checklist

- `image_gen.composition_mode` is `qwen_edit` and both live resource gates are configured.
- `preflight_qwen_edit(config)` returns no missing items after you fill model paths and install nodes.
- One 1024x576 frame with a saved character completes without OOM.
- A 3-frame run completes with one Qwen load pass after the SD1.5 background pass.
- Character identity is recognizable across at least 3 different poses.
- Held props are created by Qwen with the character, not painted into the SD1.5 background.
- Re-running the same job hits the Qwen cache.

## Fallback behavior

If Qwen dispatch preflight fails, the render uses normal one-pass ComfyUI. If an individual Qwen edit fails after preflight passes, the render keeps the background frame and continues. This feature must never crash the video render.

## Rollback

## CI / Test Dependencies

Tests for Qwen-related modules (`test_qwen_repose.py`, `test_preflight.py`)
mock all GPU interactions via `patch("torch.cuda.*")`. On CI, `torch` is
stubbed in `tests/conftest.py` — no real CUDA or torch download needed.
Run GPU-spike tests locally in the root `venv` (torch 2.11.0+cu128).

To return to the stable path, set:

```yaml
image_gen:
  composition_mode: one_pass
  qwen_edit:
    enabled: false
```

No output-path or Rust-worker cleanup is required because Qwen writes to the same frame paths and does not alter the Rust assembly path.
