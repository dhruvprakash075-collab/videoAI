# System Architecture

Video.AI is a local, staged video-generation pipeline. The current runtime is Python-first, with an optional Rust worker sidecar for job supervision.

## Entry Points

| Entry point | Purpose |
| --- | --- |
| `bootstrap_pipeline.py` | Primary CLI. Applies compatibility setup, preflight, venv guard, shutdown hooks, Sentry init, and argument parsing. |
| `utils/local_ui.py` | Local FastAPI backend for jobs, uploads, config, preflight, artifacts, chat, and A/B tools. |
| `jobs/run_worker.py` / `run_worker.bat` | Python background job worker. |
| `rust/worker` | Optional Rust sidecar that supervises the same SQLite queue. |

Do not run `python -m core.pipeline_long` as the normal entry point. Use `bootstrap_pipeline.py`.

## Main Flow

```text
Operator CLI/UI
  -> bootstrap_pipeline.py
  -> core.pipeline_long.run_long_pipeline()
  -> core.pre_production
       -> source loading / research / Director planning / outline
  -> core.segment_runner
       -> scripts -> critic -> translation -> TTS -> images -> render -> memory review
  -> core.post_production
       -> concat -> audio mastering -> thumbnail -> chapters -> manifest -> optional upload
```

## Major Packages

| Path | Role |
| --- | --- |
| `agents/` | Director, LLM client, UI state, decision helpers |
| `audio/` | IndicF5, Supertonic, OmniVoice, audio effects, mastering |
| `config/` | YAML loader and Pydantic schemas |
| `core/` | Pipeline orchestration and staged segment execution |
| `jobs/` | SQLite job queue and worker |
| `memory/` | Story memory, blackboard, project store |
| `utils/` | Source loading, research, critic, SEO, preflight, shutdown, URL security |
| `video/image_gen/` | ComfyUI client/runtime/workflow, Qwen edit, image generation |
| `video/renderer/` | Segment assembly, subtitles, final video helpers |

## Current Runtime Defaults

- TTS default: `indicf5`
- Image backend: `comfyui`
- Composition mode: `qwen_edit`
- Image size: `1344x768`
- Final video target: `1920x1080`
- Segment mode: staged loop enabled
- Heavy task concurrency: one heavy GPU task at a time

See `docs/configuration_reference.md` for exact YAML values.

## TTS

`audio/audio_proxy.py` is the unified TTS facade. The current config uses IndicF5. Supertonic and OmniVoice remain implemented and configured as local engines.

Worker-style engines use subprocesses and pass UTF-8 environment settings for Windows-safe Devanagari output.

## Image Generation

ComfyUI is managed by:

- `video/image_gen/comfyui_runtime.py`
- `video/image_gen/comfyui_client.py`
- `video/image_gen/comfyui_workflow.py`

Qwen image edit is configured under `image_gen.qwen_edit`. Admission checks protect RAM/VRAM and missing local nodes/models. Failure falls back to the normal ComfyUI frame instead of crashing the render.

## Safety

- Ollama/CrewAI calls use circuit breakers.
- GPU work uses `utils.concurrency.global_scheduler`.
- Local/remote URL access is validated through `utils/url_security.py`.
- Sentry is optional and initialized from environment variables.
- `bootstrap_pipeline.py --sentry-smoke` sends a deliberate smoke exception.
