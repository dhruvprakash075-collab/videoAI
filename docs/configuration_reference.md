# Configuration Reference

`config/config.yaml` is the live configuration source. This document summarizes the current checked-in config and the code that reads it.

## Core Models

| Key | Current value | Used by |
| --- | --- | --- |
| `models.director` | `hermes-director` | Director planning and review |
| `models.writer` | `zephyr-writer` | Writer agent (adapt mode) |
| `models.writer_scratch` | `cra-guided-7b` | Writer agent (scratch mode) |
| `models.writer_adapt` | `zephyr-writer` | Writer agent (adapt mode, explicit) |
| `models.image_engineer` | `image-engineer` | Image engineer agent |
| `models.translator` | `sarvam-translate` | Devanagari translation |
| `models.director_max_tokens` | `2048` | Max tokens for director responses |
| `ollama.host` | `http://localhost:11434` | Ollama HTTP calls |
| `ollama.keep_alive` | `3m` | Ollama model residency |
| `ollama.request_timeout` | `240` | Request timeout in seconds |
| `ollama.breaker_fails` | `3` | Consecutive failures before breaker opens |
| `ollama.breaker_cooldown_s` | `30` | Breaker cooldown in seconds |

## Language

| Key | Current value | Meaning |
| --- | --- | --- |
| `language` | `hi` | Top-level language code (overrides `tts.lang`) |

## TTS

Current default:

```yaml
tts:
  lang: hi
  engine: indicf5
  voice_samples_dir: character_voices
```

Supported engines are normalized in `audio/audio_proxy.py`. The checked-in config uses IndicF5:

```yaml
tts:
  indicf5:
    root: D:\IndicF5
    python: C:\Video.AI\venv\Scripts\python.exe
    ref_audio: character_voices/narration_ref_9s_mono24k_ref8s_mono.wav
    ref_text_file: character_voices/narration_ref_9s_mono24k.txt
    ref_text: "reference narration text"
    use_pipeline_voice_sample: false
    timeout_seconds: 900
```

Supertonic and OmniVoice remain configured fallback-capable engines:

```yaml
tts:
  supertonic:
    voice: character_voices/dhruv_narration.json
    steps: 16
    speed: 1.0
    silence_duration: 0.1
    max_chunk_length: 150
  omnivoice:
    speed: 0.5
    num_step: 16
    guidance_scale: 2.5
    ref_text: "..."
```

Devanagari processing and voice profile config:

```yaml
tts:
  devanagari:
    max_latin_ratio: 0.1
    max_retranslate_retries: 2
  voice_profile:
    sentence_gap_ms: 200
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
  guidance_scale: 3.5
  ip_adapter_scale: 0.8
  lock_seed: true
```

Upscaler config:

```yaml
image_gen:
  upscaler:
    model: none
    model_path: ""
    scale: 4
    target_width: 1920
    target_height: 1080
```

ComfyUI runtime is configured here:

```yaml
image_gen:
  comfyui:
    server: 127.0.0.1
    host: 127.0.0.1
    port: 8188
    reuse_ports: [8189]
    root: external/ComfyUI
    python: external/ComfyUI/.venv/Scripts/python.exe
    auto_start: true
    open_browser: false
    workflow_path: config/comfyui/workflows/text_to_image_api.json
    checkpoint: DreamShaper_8.safetensors
    width: 1344
    height: 768
    steps: 30
    cfg: 7.0
    sampler_name: euler
    scheduler: normal
    timeout_seconds: 300
    poll_seconds: 1.0
    auto_start_timeout: 60
    unload_after_batch: true
```

Qwen image edit is enabled but guarded by RAM/VRAM and local file checks:

```yaml
image_gen:
  qwen_edit:
    enabled: true
    backend: nunchaku
    workflow_path: config/comfyui/workflows/qwen_image_edit_api.json
    model_path: external/ComfyUI/models/diffusion_models/qwen_image_edit_2509_int4.safetensors
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

If Qwen admission fails, the render keeps the normal ComfyUI result instead of crashing.

## Video and Script Defaults

| Key | Current value |
| --- | --- |
| `video.total_duration_min` | `10` |
| `video.segment_duration_min` | `2` |
| `video.fps` | `24` |
| `video.resolution` | `1920x1080` |
| `video.output_path` | `studio_outputs/final_video.mp4` |
| `video.encoder` | `h264_nvenc` |
| `video.encoder_preset` | `p5` |
| `video.video_bitrate` | `8M` |
| `video.encoder_extra` | `-spatial-aq 1 -temporal-aq 1 -b_ref_mode 1 -bf 3` |
| `video.crossfade_duration` | `0.3` |
| `video.ken_burns` | `light` |
| `video.audio_crossfade_ms` | `200` |
| `video.generate_thumbnail` | `true` |
| `script.words_per_segment` | `100` |
| `script.min_words` | `20` |
| `script.max_words` | `600` |
| `script.dynamic_image_count` | `false` |
| `script.default_images_per_segment` | `2` |
| `script.max_images_per_segment` | `4` |
| `script.word_count_tolerance` | `0.6` |
| `script.tts_words_per_minute_hi` | `100` |
| `script.tts_words_per_minute_en` | `150` |
| `script.writer_max_tokens` | `1024` |
| `script.uncapped_scaling` | `false` |

## Subtitles

```yaml
subtitles:
  format: classic
  font: Nirmala UI
  size: 22
  color: "&H00FFFFFF&"
  language: en
```

## Narrator

```yaml
narrator:
  include_character_descriptions: false
```

## Characters

Three saved characters with descriptions and keyword sets (protagonist, mentor, guardian). Managed in `config/config.yaml` under `characters.*`.

## Scene Templates

Monster and fog scene templates for backdrop generation. Managed in `config/config.yaml` under `scene_templates.*`.

## Checkpoint, Memory, Cache

| Key | Current value | Meaning |
| --- | --- | --- |
| `checkpoint.enabled` | `true` | Enable checkpoint saves |
| `checkpoint.dir` | `studio_checkpoints` | Checkpoint directory |
| `checkpoint.max_age_hours` | `24` | Max checkpoint age |
| `memory.memory_file` | `studio_checkpoints/story_memory.json` | Story memory file path |
| `memory.llm_world_state` | `true` | Use LLM for world state |
| `cache.cache_invented_story` | `true` | Cache invented story output |

## Performance and Safety

| Key | Current value | Meaning |
| --- | --- | --- |
| `performance.max_workers` | `1` | Segment worker count |
| `performance.staged_loop` | `true` | Batch stages by task |
| `performance.lookahead_segments` | `3` | Number of segments to look ahead |
| `performance.vram_sd_threshold_gb` | `4.5` | Minimum free VRAM before SD load |
| `performance.vram_evict_wait_s` | `15` | Wait after Ollama eviction |
| `performance.max_segment_retries` | `2` | Per-segment retry budget |
| `performance.whisper_model` | `tiny` | Whisper model for transcription |
| `performance.whisper_model_final` | `base` | Whisper model for final alignment |
| `performance.ffmpeg_threads` | `8` | FFmpeg thread count |

## Audio FX

```yaml
audio_fx:
  enabled: true
  volume: 0.25
  program_loudnorm: true
  target_lufs: -14
```

SFX files (wind, rain, heartbeat, etc.) are currently missing and treated as no-ops.

## Music

```yaml
music:
  enabled: false
  ducking: true
  duck_ratio: 0.3
```

## Upload

```yaml
upload:
  enabled: false
  platform: youtube
  visibility: private
  profile_dir: chrome_profile
```

## Source, Research, Critic, SEO

Source ingestion accepts `.txt`, `.md`, `.pdf`, and `.docx`, plus URLs through `utils/source_loader.py`.

```yaml
source:
  allowed_extensions: [.txt, .md, .pdf, .docx]
  max_words: 50000
  url_timeout_s: 30

research:
  enabled: true
  sources: [wikipedia, wikimedia, rss]
  budget: 3
  timeout_s: 15
  per_source_limit: 3

critic:
  enabled: true
  threshold: 60
  max_rewrites: 2

seo:
  enabled: true
  title_max_chars: 100
  description_max_chars: 5000
  tags_count: 15
  hashtags_count: 5
  chapters_max: 50
  description_paragraphs: 2
```

## Pydantic Schema System

Config loading goes through Pydantic v2 validation in `config/config_schemas.py`.

### Validation pipeline (`config/config.py:load_config`)

1. Start with hardcoded `_default_config()` dict (see `config/config.py:73-111`)
2. Merge in `config/config.yaml` values (shallow + deep merge via `dict_merge()`)
3. Optionally merge in `projects/<name>.yaml` if a project name is passed
4. Run `validate_config()` which maps each top-level section to its Pydantic schema

### Section schemas (`config/config_schemas.py:SECTION_MODELS`)

Each YAML section is validated against a dedicated Pydantic model with defaults
that often differ from `config.yaml`:

| Section | Schema class | Schema default | YAML value |
|---------|-------------|----------------|------------|
| `critic` | `CriticConfig` | threshold=60, max_rewrites=2 | matches |
| `research` | `ResearchConfig` | budget=3, timeout_s=15 | matches |
| `seo` | `SEOConfig` | title_max_chars=100, tags_count=15 | matches |
| `source` | `SourceConfig` | max_words=50000 | matches |
| `tts` | `TTSConfig` | engine="indicf5", lang="hi" | matches |
| `models` | `ModelsConfig` | director="hermes-director", writer="zephyr-writer" | matches |
| `visual` | `VisualConfig` | num_scenes=4, style="..." | matches |
| `video` | `VideoConfig` | total_duration_min=10, fps=24, resolution="1920x1080" | matches |
| `script` | `ScriptConfig` | words_per_segment=130, dynamic_image_count=True | diverges (YAML: words_per_segment=100, dynamic_image_count=false) |
| `checkpoint` | `CheckpointConfig` | max_age_hours=0 | diverges (YAML: 24) |
| `memory` | `MemoryConfig` | — | matches |
| `ollama` | `OllamaConfig` | keep_alive="5m", breaker_fails=3, breaker_cooldown_s=30 | diverges (YAML: 3m) |
| `performance` | `PerformanceConfig` | staged_loop=False, lookahead_segments=0 | diverges (YAML: true, 3) |
| `image_gen` | `ImageGenConfig` | width=1024, height=1024, steps=12, composition_mode="one_pass" | diverges (YAML: 1344x768, 30, qwen_edit) |
| `music` | `MusicConfig` | enabled=False | matches |
| `subtitles` | `SubtitlesConfig` | format="classic", size=24, font="Arial" | diverges (YAML: 22, Nirmala UI) |
| `upload` | `UploadConfig` | enabled=False | matches |
| `narrator` | `NarratorConfig` | — | matches |
| `cache` | `CacheConfig` | — | matches |
| `audio_fx` | `AudioFxConfig` | volume=0.25 | matches |

The schema defaults are what the pipeline uses when a key is absent from YAML.
**The checked-in `config.yaml` overrides many of these defaults** — the schema
is the canonical source of truth for validation, but the YAML is the live config.

### Unknown key enforcement

`validate_config()` in `config/config_schemas.py:595` rejects any top-level key
not in `ALLOWED_KEYS` (line 576) with `FatalError`. This prevents silent typos.

### DecisionRecord

`config/config_schemas.py:711` — Tracks video production decisions with
provenance (default/director/writer/user/cli_flag) and lock semantics:

```python
record.set("segment_count", 5, "director")  # director proposes
record.set("segment_count", 3, "user")       # user overrides
```

Conflict resolution (`resolve_conflicts()`) handles locked-field contradictions:
- If `segment_count` is locked, it recomputes `total_duration_min`
- If `total_duration_min` is locked, it recomputes `segment_count`
- If both locked, it adjusts `segment_duration_min`

### ConfigOverlay

`config/config_schemas.py:497` — Runtime overlay produced by the Director agent
after vision analysis. Applied on top of `config.yaml` + project config during
pipeline execution. Fields: characters, visual, tts, script, subtitles, pacing,
video, upload.

### Project config merging

`config/config.py:47-70` — When a `project_name` is passed to `load_config()`,
it reads `projects/<sanitized_name>.yaml` and deep-merges it over the base
config. The project file path is validated against the `projects/` directory to
prevent path traversal.

## Sentry

Sentry is optional and configured through environment variables, not YAML:

```powershell
$env:SENTRY_DSN="..."
$env:SENTRY_ENVIRONMENT="local"
.\venv\Scripts\python.exe bootstrap_pipeline.py --sentry-smoke
```

The integration lives in `utils/sentry.py` and is initialized by `bootstrap_pipeline.py` and `core/main.py`.

## CI / Test Dependencies

CI installs lightweight packages only (`pytest`, `pydantic`, `httpx`,
`fastapi`, `pydub`, `soundfile`, `psutil`, `playwright`, `langgraph`, etc.). Heavy
packages (`torch`, `crewai`, `faster_whisper`) are stubbed via
`tests/conftest.py:_install_optional_dependency_stubs()`. See
`docs/testing_and_linting.md` for details.
