# Configuration Reference

`config/config.yaml` is the live configuration source. This document summarizes the current checked-in config and the code that reads it.

## Core Models

| Key | Current value | Used by |
| --- | --- | --- |
| `models.director` | `hermes-director` | Director planning and review |
| `models.writer` | `zephyr-writer` | Writer agent |
| `models.translator` | `sarvam-translate` | Devanagari translation |
| `ollama.host` | `http://localhost:11434` | Ollama HTTP calls |
| `ollama.keep_alive` | `3m` | Ollama model residency |

## TTS

Current default:

```yaml
tts:
  lang: hi
  engine: indicf5
```

Supported engines are normalized in `audio/audio_proxy.py`. The checked-in config uses IndicF5:

```yaml
tts:
  indicf5:
    root: D:\IndicF5
    python: C:\Video.AI\venv\Scripts\python.exe
    ref_audio: character_voices/narration_ref_9s_mono24k_ref8s_mono.wav
    ref_text: "reference narration text"
    timeout_seconds: 900
```

Supertonic and OmniVoice remain configured fallback-capable engines:

```yaml
tts:
  supertonic:
    voice: character_voices/dhruv_narration.json
    steps: 16
    speed: 1.0
    max_chunk_length: 150
  omnivoice:
    speed: 0.5
    num_step: 16
    guidance_scale: 2.5
```

## Image Generation

Current backend:

```yaml
image_gen:
  backend: comfyui
  composition_mode: qwen_edit
  width: 1344
  height: 768
  steps: 30
```

ComfyUI runtime is configured here:

```yaml
image_gen:
  comfyui:
    server: 127.0.0.1
    host: 127.0.0.1
    port: 8188
    root: external/ComfyUI
    python: external/ComfyUI/.venv/Scripts/python.exe
    auto_start: true
    workflow_path: config/comfyui/workflows/text_to_image_api.json
    checkpoint: DreamShaper_8.safetensors
    width: 1344
    height: 768
    steps: 30
    cfg: 7.0
```

Qwen image edit is enabled but guarded by RAM/VRAM and local file checks:

```yaml
image_gen:
  qwen_edit:
    enabled: true
    backend: nunchaku
    workflow_path: config/comfyui/workflows/qwen_image_edit_api.json
    model_path: external/ComfyUI/models/diffusion_models/qwen_image_edit_2509_int4.safetensors
    min_available_ram_gib: 8.0
    min_free_vram_mib: 5000
    character_threshold: 0.05
```

If Qwen admission fails, the render keeps the normal ComfyUI result instead of crashing.

## Video and Script Defaults

| Key | Current value |
| --- | --- |
| `video.total_duration_min` | `10` |
| `video.segment_duration_min` | `2` |
| `video.fps` | `24` |
| `video.resolution` | `1920x1080` |
| `video.encoder` | `h264_nvenc` |
| `script.words_per_segment` | `100` |
| `script.default_images_per_segment` | `2` |
| `script.max_images_per_segment` | `4` |

## Performance and Safety

| Key | Current value | Meaning |
| --- | --- | --- |
| `performance.max_workers` | `1` | Segment worker count |
| `performance.staged_loop` | `true` | Batch stages by task |
| `performance.vram_sd_threshold_gb` | `4.5` | Minimum free VRAM before SD load |
| `performance.vram_evict_wait_s` | `15` | Wait after Ollama eviction |
| `performance.max_segment_retries` | `2` | Per-segment retry budget |

## Source, Research, Critic, SEO

Source ingestion accepts `.txt`, `.md`, `.pdf`, and `.docx`, plus URLs through `utils/source_loader.py`.

```yaml
research:
  enabled: true
  sources: [wikipedia, wikimedia, rss]
  budget: 3
  timeout_s: 15
critic:
  enabled: true
  threshold: 60
  max_rewrites: 2
seo:
  enabled: true
  title_max_chars: 100
  tags_count: 15
  hashtags_count: 5
```

## Sentry

Sentry is optional and configured through environment variables, not YAML:

```powershell
$env:SENTRY_DSN="..."
$env:SENTRY_ENVIRONMENT="local"
.\venv\Scripts\python.exe bootstrap_pipeline.py --sentry-smoke
```

The integration lives in `utils/sentry.py` and is initialized by `bootstrap_pipeline.py` and `core/main.py`.
