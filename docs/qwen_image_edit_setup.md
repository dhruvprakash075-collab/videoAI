# Qwen-Image-Edit two-pass setup

This feature is optional and ships off by default. It keeps the current fast image path unchanged until you explicitly enable `image_gen.composition_mode: qwen_edit` and `image_gen.qwen_edit.enabled: true`.

## What it does

1. **Pass 1:** ComfyUI + SD1.5 generates every background as a complete scene. No empty character hole is reserved.
2. **Pass 2:** Qwen-Image-Edit-2509 loads once and inserts the saved character into frames that have `char_presence` above the configured threshold.
3. Frames without a saved character stay exactly as the Pass 1 background.
4. Qwen handles character placement, depth/occlusion, and any prop the character is holding.

The output frame path is unchanged. Qwen overwrites/copies back to the same frame path so the Rust asset manifest and ffmpeg assembly can keep using the current image locations.

## Recommended 6 GB setup

Use **Nunchaku INT4** first. It should use the least VRAM and avoid swapping the model for every frame because the implementation batches by model: all backgrounds first, then all Qwen edits.

Suggested config block:

```yaml
image_gen:
  composition_mode: one_pass   # keep default off
  qwen_edit:
    enabled: false
    backend: nunchaku
    workflow_path: config/comfyui/workflows/qwen_image_edit_api.json
    model_path: ""
    lightning_lora: ""
    steps: 8
    cfg: 1.0
    denoise: 0.6
    max_resolution: 1024
    youtube_aspect: "16:9"
    vram_offload: true
    trigger: any_character
    character_threshold: 0.05
    cache_dir: .qwen_edit_cache
    required_custom_nodes:
      - ComfyUI-nunchaku
```

To enable after installation:

```yaml
image_gen:
  composition_mode: qwen_edit
  qwen_edit:
    enabled: true
    model_path: external/ComfyUI/models/diffusion_models/qwen_image_edit_2509_int4.safetensors
```

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

## Smoke test checklist

- `image_gen.composition_mode` is still `one_pass` on main config before testing.
- `preflight_qwen_edit(config)` returns no missing items after you fill model paths and install nodes.
- One 1024x576 frame with a saved character completes without OOM.
- A 3-frame run completes with one Qwen load pass after the SD1.5 background pass.
- Character identity is recognizable across at least 3 different poses.
- Held props are created by Qwen with the character, not painted into the SD1.5 background.

## Fallback behavior

If Qwen preflight fails, ComfyUI errors, or no character reference exists, the render keeps the background frame and continues. This feature must never crash the video render.
