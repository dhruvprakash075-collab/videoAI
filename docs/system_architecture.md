# System Architecture

This document describes the structure and execution flow of the **Video.AI** local video-generation pipeline.

---

## 1. Entry Points

> **CRITICAL**: Always run through `bootstrap_pipeline.py`. Running `python -m core.pipeline_long` directly is **forbidden** — bootstrap applies Win32 UTF-8 patches, rich console fixes, FFmpeg PATH injection, CrewAI telemetry-off, `OPENAI_MAX_RETRIES=0`, runs preflight, and registers graceful shutdown (Ctrl-C Ollama eviction).

| Entry Point | Purpose |
|---|---|
| `bootstrap_pipeline.py` | **Primary CLI entry point.** Applies all patches, preflight, and args. Supports `--source <path-or-URL>` (v6 upload-source mode). |
| `studio_tui.py` | Operator TUI (Textual). `venv\Scripts\python.exe studio_tui.py` |
| `run.bat` | Windows menu launcher: UI / CLI / Tests. |
| `utils/local_ui.py` | FastAPI backend for the React dashboard (port 8000). |
| `train_lora.py` | Standalone LoRA face-lock training. |

---

## 2. Directory Structure

```
c:\Video.AI
├── bootstrap_pipeline.py   # ← MANDATORY CLI entry point
├── agents/                 # CrewAI agent definitions
│   ├── director_agent.py       # DirectorAgent (1,772 lines). 2026-06-02 split.
│   ├── ui_state.py             # UIState class (split from director_agent.py)
│   ├── llm_client.py           # DirectorLlmClient (Ollama raw plumbing)
│   └── decision_engine.py      # Authority hierarchy (default < director < writer < user)
├── audio/                  # TTS, SFX, Loudnorm mastering
│   ├── audio_proxy.py          # Unified TTS facade (OmniVoice primary, edge-tts fallback)
│   ├── audio_fx.py             # Loudnorm two-pass, music ducking, SFX mixing
│   ├── omnivoice_worker.py     # OmniVoice persistent worker
│   └── f5_worker.py            # F5-TTS persistent worker
├── config/                 # YAML config + Pydantic schema validation
│   ├── config.yaml             # ← LIVE source of truth for all tunables
│   ├── config.py               # Loader (load_config)
│   └── config_schemas.py       # Pydantic schema
├── core/                   # Pipeline orchestration
│   ├── pipeline_long.py        # Thin orchestrator (re-exports backward-compat names)
│   ├── pipeline_graph.py       # SegmentState TypedDict + graph wiring (v6 Phase 4)
│   ├── pre_production.py       # Director planning phase (research, outline, LoRA)
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
    ├── image_gen/image_gen.py  # Stable Diffusion + LoRA face-lock + VRAM OOM ladder
    └── renderer/assembler.py   # Ken Burns pan/zoom + Devanagari subtitle overlay + FFmpeg
```

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
  │     ├─► Director Agent           (story outline, style anchors, LoRA assignment)
  │     └─► utils/source_splitter.py (v6: split uploaded source into SegmentChunks)
  │
  ├─► core/segment_runner.py  ← per-segment loop (uses SegmentState from pipeline_graph.py)
  │     ├─► Writer Agent             (script generation, or bypass if source_chunk set)
  │     ├─► utils/critic.py          (5-dim rubric: Hook/Arc/Pacing/Retention/TTS ≥ 60/100)
  │     ├─► audio/audio_proxy.py     (OmniVoice TTS primary, edge-tts fallback)
  │     └─► video/image_gen/image_gen.py  (SD + LoRA face-lock, 3-tier OOM recovery)
  │
  └─► core/post_production.py
        ├─► audio/audio_fx.py        (Loudnorm two-pass, music ducking, SFX mix)
        ├─► video/renderer/assembler.py  (Ken Burns MP4 + Devanagari subtitle track)
        ├─► utils/seo_generator.py   (YouTube SEO metadata)
        └─► Manifest verification and QC
```
