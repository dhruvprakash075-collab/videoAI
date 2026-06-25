# System Architecture

This document describes the structure and execution flow of the **Video.AI** local video-generation pipeline.

---

## 1. Entry Points

> **CRITICAL**: Always run through `bootstrap_pipeline.py`. Running `python -m core.pipeline_long` directly is **forbidden** — bootstrap applies Win32 UTF-8 patches, rich console fixes, FFmpeg PATH injection, CrewAI telemetry-off, `OPENAI_MAX_RETRIES=0`, runs preflight, registers graceful shutdown (Ctrl-C Ollama eviction), and now enforces the **venv guard** (rejects system Python 3.14 — requires `venv\Scripts\python.exe`).

| Entry Point | Purpose |
|---|---|
| `bootstrap_pipeline.py` | **Primary CLI entry point.** Applies all patches, preflight, and args. Supports `--source <path-or-URL>` (v6 upload-source mode). |
| `studio_tui.py` | Operator TUI (Textual). `venv\Scripts\python.exe studio_tui.py` |
| `run.bat` | Windows menu launcher: UI / CLI / Tests. |
| `utils/local_ui.py` | FastAPI backend for the React dashboard (port 8000). |

> **2026-06-04:** `train_lora.py` removed. Character consistency is now achieved
> via IP-Adapter FLUX v2 referencing per-character master portraits (see §5b).

---

## 2. Directory Structure

```
c:\Video.AI
├── bootstrap_pipeline.py   # ← MANDATORY CLI entry point
├── agents/                 # CrewAI agent definitions
│   ├── director_agent.py       # DirectorAgent (2,218 lines). 2026-06-02 split.
│   ├── ui_state.py             # UIState class (split from director_agent.py)
│   ├── llm_client.py           # DirectorLlmClient (Ollama raw plumbing)
│   └── decision_engine.py      # Authority hierarchy (default < director < writer < user)
├── audio/                  # TTS, SFX, Loudnorm mastering
│   ├── audio_proxy.py          # Unified TTS facade (Supertonic 3 default → omnivoice fallback)
│   ├── audio_fx.py             # Loudnorm two-pass, music ducking, SFX mixing
│   ├── omnivoice_worker.py     # OmniVoice persistent worker (fallback engine)
│   ├── supertonic_worker.py    # Supertonic 3 persistent worker (CPU ONNX, has danda fix P6-1, 2026-06-04)
├── config/                 # YAML config + Pydantic schema validation
│   ├── config.yaml             # ← LIVE source of truth for all tunables
│   ├── config.py               # Loader (load_config)
│   └── config_schemas.py       # Pydantic schema
├── core/                   # Pipeline orchestration
│   ├── pipeline_long.py        # Thin orchestrator (re-exports backward-compat names)
│   ├── pipeline_graph.py       # SegmentState TypedDict + graph wiring (v6 Phase 4)
│   ├── pre_production.py       # Director planning phase (research, outline, master portraits)
│   ├── segment_runner.py       # Per-segment loop + VRAM eviction + GPU scheduling
│   ├── post_production.py      # Concat, thumbnail, manifest, QC
│   └── main.py                 # CrewAI agent factories (create_director, create_writer)
├── memory/                 # Story continuity tracking
│   ├── memory.py               # Story memory + world state
│   ├── blackboard.py           # Shared in-flight state board
│   └── project_store.py        # Per-series persistent store
├── utils/                  # Cross-cutting utilities
│   ├── source_loader.py        # v6 Phase 1: loads .txt/.md/.pdf/.docx/URL/paste
│   ├── source_splitter.py      # v6 Phase 2: splits source into SegmentChunks
│   ├── researcher.py           # v6 Phase 3: Wikipedia REST / Wikimedia REST / RSS
│   ├── critic.py               # v6 Phase 4: 5-dim rubric (Hook/Arc/Pacing/Retention/TTS)
│   ├── seo_generator.py        # v6 Phase 5: YouTube SEO metadata
│   ├── crewai_breaker.py       # Per-model circuit breaker for all CrewAI kickoffs
│   ├── concurrency.py          # global_scheduler (GPU slots) + crewai_lock (RLock)
│   ├── checkpoint.py           # Resumable run state (crash recovery)
│   ├── preflight.py            # Startup readiness checks (Ollama, VRAM, disk, FFmpeg)
│   ├── shutdown.py             # Graceful SIGINT/SIGTERM/SIGBREAK handler
│   ├── ollama_client.py        # OllamaClient + B1 per-model circuit breaker
│   └── compatibility.py        # Win32 UTF-8 console patches
└── video/                  # Image generation + video rendering
    ├── image_gen/image_gen.py  # ComfyUI primary + Bonsai 4B ternary fallback + IP-Adapter v2 + 2-tier OOM
    ├── image_gen/ip_adapter.py # IPAdapterManager singleton (per-character master portraits)
    ├── image_gen/comfyui_client.py   # ComfyUI HTTP client
    ├── image_gen/comfyui_runtime.py  # ComfyUI process management
    ├── image_gen/comfyui_workflow.py # ComfyUI workflow patching
    └── renderer/assembler.py   # Ken Burns pan/zoom + Devanagari subtitle overlay + FFmpeg
```

---

## 4. TTS Subsystem Detail (2026-06-04)

### Engines
| Engine | Type | VRAM | Realtime | Cost | When to use |
|---|---|---|---|---|---|
| **Supertonic 3** (default) | CPU ONNX | 0 GB | 5.1x | Free (MIT + OpenRAIL-M) | All Hindi narration |
| OmniVoice | GPU DiT | ~2 GB | 1.2x | Free | Higher-quality fallback |

### Fallback chain
`audio/audio_proxy.py::tts_generate()` tries **supertonic → omnivoice** in
order. Failure of any one engine
silently cascades to the next. To disable fallback, set `tts.engine` to the
exact engine name (no `~` prefix) and remove the chain in `audio_proxy.py`.

### Worker pattern
All engines use **persistent `--serve` worker subprocesses** (mirroring
`omnivoice_worker.py`'s protocol). The parent process spawns once, then
sends JSON-over-stdin requests and receives binary WAV on stdout. This
avoids the ~3s ONNX model load on every TTS call.

**Subprocess encoding gotcha (Windows):** the worker subprocess inherits
`sys.stdout` from PowerShell which is cp1252. When the parent spawns the
worker, pass `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` so the
worker can `print()` Devanagari text without UnicodeDecodeError. See
`audio/supertonic_worker.py:spawn()` and `omnivoice_worker.py:spawn()`.

### Custom voice cloning (DIY)
See `docs/voice_cloning.md` for the full pipeline. In short: any
`.wav` reference audio → `external/supertonic_embed/optimize_style.py
<name>` → `.json` voice style → drop in `character_voices/` → set
`tts.supertonic.voice` to the file path.

---

## 4b. Image Generation Subsystem Detail (2026-06-16)

### Primary Backend: ComfyUI
**ComfyUI** (auto-started local instance) is the primary image generation backend.
Runs `DreamShaper_8.safetensors` checkpoint via custom workflows.
Supports multiple composition modes:
- `one_pass` (default) — single T2I workflow
- `qwen_edit` — two-pass: background → Qwen-Image-Edit character insertion

ComfyUI config in `image_gen.comfyui` block: server, root, python venv, workflow path, checkpoint, steps, CFG.

### Fallback Backend: Bonsai Image 4B (ternary, gemlite 2-bit)
`prism-ml/bonsai-image-ternary-4B-gemlite-2bit` — FLUX-style distilled model.
Used when ComfyUI is unavailable or fails. Default settings: `steps=4`, `guidance_scale=3.5`, `width=height=1024`.
No negative prompt (FLUX-style models do not use them). Sequential VRAM — peak **~3.5 GB** on RTX 4050 6 GB.

### Character face consistency — IP-Adapter FLUX v2
**`XLabs-AI/flux-ip-adapter-v2`** references a per-character **master portrait** to keep faces consistent across frames and across future videos in the same project. Scale defaults to `0.8` (configurable via `image_gen.ip_adapter_scale`). Works with both ComfyUI and Bonsai backends.

**Lazy portrait generation**: on the first frame in a project where a character has `char_presence ≥ 0.3` and no existing `master_portrait_path`, the pipeline generates 3 candidates using `portrait_prompt` (or `visual_description` as fallback) and picks the best via CLIP image-text scoring. Stored at `studio_projects/{project_id}/characters/{char_key}/master.png` with SHA256 hash recorded in `project_store`.

**Dominant character per frame**: if multiple characters are present, only the one with the highest weight (≥ 0.3) gets the IP-Adapter reference; secondary characters get prompt description only.

### OOM recovery (2-tier)
| Tier | When | Action |
|---|---|---|
| 1 (default) | first attempt | normal Bonsai call, 4 steps |
| 2 (fallback) | Tier 1 OOM | retry with `max(2, steps * 0.5)` steps |
| skip + log | Tier 2 OOM | record OOM event in `oom_report.json`, frame placeholder |

The OOM report is accessible via `image_gen.get_oom_report()`. See `runtime_safety_guide.md` §4 for the full ladder.

### File layout
```
video/image_gen/
├── image_gen.py              # ComfyUI + Bonsai + IP-Adapter wiring, generate_images(..., project_id=...)
├── ip_adapter.py             # IPAdapterManager singleton (get_ip_adapter, unload_ip_adapter)
├── comfyui_client.py         # ComfyUIClient (HTTP API)
├── comfyui_runtime.py        # ComfyUI process lifecycle (start/stop/health)
└── comfyui_workflow.py       # WorkflowPatcher + default workflow creation
core/pre_production.py        # generate_master_portrait(char_key, project_id, char_data, config, dry_run)
                              # _score_with_clip(prompt, image) — best-of-3 selection
                              # _record_portrait_to_store(char_key, project_id, png_path)
```

---

---

## 3. Execution Flow

```
Operator (CLI/UI)
  │
  ▼
bootstrap_pipeline.py      ← applies patches + runs preflight
  │
  ▼
core/pipeline_long.py      ← thin orchestrator (never run directly)
  │
  ├─► core/pre_production.py
  │     ├─► utils/researcher.py      (Wikipedia/Wikimedia/RSS web research)
  │     ├─► Director Agent           (story outline, style anchors, character definitions with portrait_prompt)
  │     └─► utils/source_splitter.py (v6: split uploaded source into SegmentChunks)
  │
  ├─► core/segment_runner.py  ← per-segment loop (uses SegmentState from pipeline_graph.py)
  │     ├─► Writer Agent             (script generation, or bypass if source_chunk set)
  │     ├─► utils/critic.py          (5-dim rubric: Hook/Arc/Pacing/Retention/TTS ≥ 60/100)
  │     ├─► audio/audio_proxy.py     (TTS dispatch: supertonic → omnivoice)
  │     │     └─► supertonic_worker.py  (default, CPU ONNX, ~5x realtime, has danda fix for Hindi)
  │     └─► video/image_gen/image_gen.py  (ComfyUI primary + Bonsai fallback + IP-Adapter v2, 2-tier OOM)
  │
  └─► core/post_production.py
        ├─► audio/audio_fx.py        (Loudnorm two-pass, music ducking, SFX mix)
        ├─► video/renderer/assembler.py  (Ken Burns MP4 + Devanagari subtitle track)
        ├─► utils/seo_generator.py   (YouTube SEO metadata)
        └─► Manifest verification and QC
```
