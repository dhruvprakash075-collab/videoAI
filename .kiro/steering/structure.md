# Project Structure

## Entry points

- `bootstrap_pipeline.py` — **primary entry point.** Applies compatibility patches, parses CLI args, then calls `core/pipeline_long.py`.
- `run_pipeline.py`, `run.bat`, `*.ps1` — convenience wrappers around bootstrap for a specific topic.
- `utils/local_ui.py` — FastAPI server (localhost:8000) backing the dashboard.
- `train_lora.py` — standalone LoRA training for character face-lock.

## Top-level layout

```
agents/        CrewAI agent definitions (director_agent.py — 2619 lines, god module;
               executive_agent.py — effectively dead)
audio/         TTS, RVC, SFX/mastering (audio_proxy.py, audio_fx.py, tts/, fx/)
config/        config.yaml + loaders/validators (config.py, config_schema(s).py)
core/          Pipeline orchestration:
                 main.py              — CrewAI agent factory
                 pipeline_long.py     — thin orchestrator (644 lines, was 2830)
                 pre_production.py    — Director phase (research, analysis, outline, LoRA)
                 segment_runner.py    — per-segment loop, retry budget
                 post_production.py   — concat, thumbnail, chapters, manifest, QC
dashboard/     React 19 + Vite frontend (operator UI)
memory/        Story memory & world state (continuity tracking)
utils/         Cross-cutting helpers (see below)
video/         Image generation + rendering (image_gen/, renderer/)
projects/      Per-series config overrides ({name}.yaml)
prompts.yaml   LLM prompt templates
styles.yaml    Visual style presets (resolved by style_resolver.py)
_archive/      Moved-but-not-deleted items (see _archive/README.md):
                 tts_audiobook/   — sibling project (219 files)
                 pipeline_env/    — unused venv (180MB)
                 rvc_env/         — opt-in RVC venv (680MB)
```

## Generated / runtime directories (do not treat as source)

```
studio_outputs/      Final rendered videos
studio_checkpoints/  Resume checkpoints + story_memory.json
cache/, hf_cache/    Vision cache and HuggingFace model cache
logs/                Run logs
temp_srt_files/, sfx/, static/   Intermediate/asset artifacts
character_voices/    Reference voice samples for TTS/RVC
venv/                Python virtualenv (only one — others archived)
ffmpeg-8.1.1-essentials_build/   Bundled FFmpeg binaries
```

## The `utils/` layer

Shared functionality, re-exported through `utils/__init__.py`:
- `compatibility.py` — Windows/encoding patches (`apply_all_patches`).
- `concurrency.py` — `global_scheduler` for GPU-aware task scheduling, `crewai_lock` (RLock).
- `crewai_breaker.py` — **NEW (2026-06):** per-model circuit-breaker wrapper for
  CrewAI `kickoff()`. Reuses `OllamaClient._breaker()` so a failing model opens
  ONE breaker whether called via `generate()` or `crew.kickoff()`.
  Exposes `guarded_crewai_kickoff(crew, model_name, timeout_s, lock)` and
  `BreakerOpen` exception. Wired into `core/segment_runner.py`,
  `utils/story_planner.py`, `utils/context_manager.py`.
- `checkpoint.py` — resumable run state.
- `story_planner.py`, `scene_director.py`, `emotion_control.py` — narrative/visual planning.
- `specialized_models.py` — fast review + image-prompt models (NOT yet on B1 breaker;
  has its own urllib loop, low priority because image-engineer degrades gracefully).
- `quality_check.py`, `retry_manager.py`, `vision_cache.py`, `web_search.py`,
  `context_manager.py`, `env_manager.py`.

## Architecture flow

```
CLI/UI → bootstrap → pipeline_long → Director (plan) → Writer (script) → Reviewer
       → translate → TTS/RVC/SFX (audio/) → Stable Diffusion (video/image_gen)
       → render segments → concatenate (video/renderer) → final MP4
       ↕ StoryMemory (memory/) for continuity, Checkpoints for resume
```

## Conventions

- **Imports**: pipeline modules add the repo root to `sys.path` and import compatibility patches before heavy deps. Preserve this ordering when editing entry points.
- **Module headers**: files start with a one-line docstring describing their role (e.g. `"""main.py - CrewAI agent factory..."""`). Match this style.
- **Config-driven**: read tunables from the loaded config dict (`config.get("section", {}).get("key", default)`) rather than hardcoding. Add new options to `config.yaml` and the schema.
- **Optional dependencies**: wrap optional/fragile imports (image_gen, LoRA, context_manager) in `try/except ImportError` with a `log.warning` and a graceful fallback — follow the existing pattern.
- **Logging**: use the module logger (`log = logging.getLogger(__name__)`), not `print`, inside library code. CLI status banners in entry points may use `print`.
- **Thread safety**: CrewAI `kickoff()` and Director translation are serialized with locks; keep GPU/LLM-heavy work behind the existing locks/scheduler.
- **Naming**: snake_case for Python files/functions; kebab/lowercase for config keys; PascalCase `.jsx` components in the dashboard.
- **Windows paths**: avoid POSIX-only path assumptions; prefer `pathlib.Path`.
