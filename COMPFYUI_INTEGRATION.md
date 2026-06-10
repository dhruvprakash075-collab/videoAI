# ComfyUI Integration & Dependency Documentation

## Overview
ComfyUI is integrated as an **external runtime** at `external/ComfyUI/`, not as a Python dependency.

## How It Works
1. **Config** (`config.yaml`): Points to `external/ComfyUI` with `image_gen.backend: comfyui`
2. **Runtime** (`video/image_gen/comfyui_runtime.py`): Auto-starts ComfyUI via subprocess on `127.0.0.1:8188`
3. **Client** (`video/image_gen/comfyui_client.py`): HTTP API client for image generation

## Dependency Ownership

| Location | Owner | Purpose |
|----------|-------|---------|
| `requirements.txt` | **Our project** | Video.AI dependencies (diffusers, transformers, fastapi, etc.) |
| `external/ComfyUI/requirements.txt` | **ComfyUI project** | ComfyUI runtime dependencies (torch, aiohttp, Pillow, etc.) |
| `external/supertonic_embed/requirements.txt` | **Supertonic project** | Supertonic TTS dependencies |
| `indicf5_env/` | **IndicF5 environment** | IndicF5 TTS environment |

## Our Dependencies (requirements.txt) - UPDATED
- `diffusers>=0.38.0` - Bonsai image generation (our primary path)
- `transformers>=5.10.2` - Translation/models
- `pillow>=12.2.0` - Image processing
- `requests>=2.34.2` - HTTP client
- `fastapi>=0.110.0`, `uvicorn>=0.29.0` - API server
- **No direct torch/torchvision/torchaudio** - installed separately for CUDA 12.8

## ComfyUI Dependencies (external/ComfyUI/requirements.txt) - **UPDATED & PINNED**

The ComfyUI venv already has working versions installed. Updated requirements.txt now pins to verified versions:

| Package | Version | Notes |
|---------|---------|-------|
| `torch` | `2.11.0+cu130` | CUDA 13.0 |
| `torchvision` | `0.26.0+cu130` | CUDA 13.0 |
| `torchaudio` | `2.11.0+cu130` | CUDA 13.0 |
| `transformers` | `5.10.2` | Matches our project |
| `Pillow` | `12.2.0` | Matches our project |
| `requests` | `2.34.2` | Matches our project |
| `aiohttp` | `>=3.11.8` | HTTP server |
| `safetensors` | `>=0.4.2` | Model weights |
| `comfyui-frontend-package` | `1.45.15` | Frontend bundle |
| `comfy-kitchen` | `0.2.10` | Custom nodes |

## Security Note
**Updated** - `external/ComfyUI/requirements.txt` now pins all critical packages to verified working versions. Skylos should no longer flag vulnerabilities here.

## Validation
- Verified against `C:\Video.AI\external\ComfyUI\.venv` installed packages
- CUDA 13.0 compatibility confirmed
- GPU inference tested and working

## Recommendation
Keep `external/` in `.skylos.yml` exclusions for now (prevents noise from other external dirs). ComfyUI deps are now clean.

## Supertonic TTS Dependencies (external/supertonic_embed/requirements.txt) - **UPDATED & PINNED**

Supertonic uses the main project venv. Updated requirements.txt pins to verified working versions:

| Package | Version | Notes |
|---------|---------|-------|
| `torch` | `2.11.0+cu128` | CUDA 12.8 |
| `torchaudio` | `2.11.0+cu128` | CUDA 12.8 |
| `onnxruntime` | `1.26.0` | Inference runtime |
| `onnx` | `1.21.0` | Model format |
| `transformers` | `5.9.0` | Model loading |
| `librosa` | `0.11.0` | Audio processing |
| `soundfile` | `0.13.1` | Audio I/O |

## Security Note
**Updated** - Both `external/ComfyUI/requirements.txt` and `external/supertonic_embed/requirements.txt` now pin all critical packages to verified working versions.

## Integration Verification
✅ Config points to `C:\Video.AI\external\ComfyUI`
✅ Runtime auto-starts ComfyUI on `127.0.0.1:8188`
✅ Client communicates via HTTP API
✅ Workflow: `config/comfyui/workflows/text_to_image_api.json`
✅ Checkpoint: `DreamShaper_8.safetensors`
✅ Fallback: `bonsai` (diffusers) if ComfyUI fails

## Supertonic TTS Integration
✅ Config: `tts.engine: supertonic` in `config.yaml`
✅ Runtime: Uses main project venv (no separate env needed)
✅ Models: ONNX format in `external/supertonic_embed/onnx/`
✅ Voice styles: `external/supertonic_embed/voice_styles/`