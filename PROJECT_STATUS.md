# Video.AI — Project Status & Current State

> **Last Updated:** June 1, 2026  
> **Covers:** Full implementation status, architecture, recent changes, known issues, and roadmap.

---

## Executive Summary

Video.AI is a **local-first Dynamic Narrative Video-Generation Engine** that turns a topic or story file into a fully narrated, subtitled, multi-segment MP4 video. It runs entirely on a single Windows machine with an RTX 4050 6GB laptop GPU.

**Current state:** The pipeline is **fully functional end-to-end**. A real run (`--topic "A ghost ship appears in the fog" --yes --no-resume --duration 2`) has been verified producing correct output. All major planned improvements (Phases A/B/D) are implemented. 234 automated tests pass. The Studio TUI, React dashboard, and CLI are all operational.

---

## 1. Hardware & Platform

| Component | Spec |
|-----------|------|
| GPU | NVIDIA RTX 4050 Laptop (6GB VRAM) |
| CPU | AMD Ryzen 7 7840HS |
| RAM | 16GB |
| OS | Windows 11 |
| Python | 3.12 (in `venv/`) |
| CUDA | 12.8 (PyTorch 2.11) |

**Critical constraint:** Only ONE large model fits in 6GB VRAM at a time. All GPU work is serialized (`max_workers: 1`).

---

## 2. Tech Stack (Current)

| Layer | Technology | Version |
|-------|-----------|---------|
| Agent Orchestration | CrewAI | 1.14.5 |
| LLM Serving | Ollama | localhost:11434 |
| Image Generation | diffusers (Lykon/AnyLoRA) | 0.37.1 |
| Deep Learning | PyTorch + CUDA 12.8 | 2.11.0+cu128 |
| TTS (primary) | OmniVoice (voice cloning) | Custom worker |
| TTS (fallback) | edge-tts | Latest |
| TTS (optional) | F5-TTS Hindi | SPRINGLab |
| ASR | faster-whisper | Latest |
| Audio | pydub, soundfile, FFmpeg 8.1.1 | Bundled |
| Video Encoding | FFmpeg h264_nvenc | Ada NVENC p5 |
| Translation | Ollama (sarvam-translate) | Gemma3-based |
| Web API | FastAPI + uvicorn | localhost:8000 |
| Frontend | React 19 + Vite 8 + Tailwind 4 | dashboard/ |
| TUI | Textual | 8.2.7 |
| Type Checker | Rich | 14.2.0 |
| Config | PyYAML + Pydantic | config.yaml |

---

## 3. Ollama Models (Currently Installed)

| Alias | Base Model | Role | Status |
|-------|-----------|------|--------|
| `hermes-director` | Hermes 3 Llama 3.1 8B Q4_K_S | Story planning, JSON structure | ✅ Created |
| `zephyr-writer` | Zephyr 7B Beta Q4_K_M | Script writing (default + adapt) | ✅ Created |
| `cra-guided-7b` | CRA Guided 7B Q4_K_M | Creative invention mode | ✅ Created |
| `sarvam-translate` | Sarvam (Gemma3-based) | English → Devanagari | ✅ Created |
| `script-reviewer` | Qwen2.5-3B (1.9GB) | Fast script review | ✅ Created |
| `image-engineer` | 7B (4.7GB) | Image prompt generation | ✅ Created |

All GGUFs stored in `C:\models\`. Aliases created via `ollama create <name> -f Modelfile.<name>`.

---

## 4. Entry Points & How to Run

### Primary: TUI (recommended)
```
Double-click TUI.bat
```
- Auto-starts Ollama if not running
- Opens the Textual Studio Console
- Type a topic and press Enter

### CLI (headless / scripted)
```powershell
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your Topic" --duration 10
venv\Scripts\python.exe bootstrap_pipeline.py --file story.txt
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Topic" --dry-run --yes
venv\Scripts\python.exe bootstrap_pipeline.py --topics-file batch.txt --yes
```

### Dashboard (web UI)
```powershell
venv\Scripts\python.exe utils\local_ui.py          # API on :8000
cd dashboard && npm run dev                         # Vite on :5173
```

### Key CLI Flags
| Flag | Purpose |
|------|---------|
| `--topic "..."` | Video topic |
| `--file path.txt` | Story file input |
| `--duration N` | Total duration (minutes) |
| `--dry-run` | Preview without generating video |
| `--no-resume` | Ignore checkpoints, start fresh |
| `--skip-rvc` | Skip voice conversion |
| `--project name` | Load projects/{name}.yaml overrides |
| `--director-mode` | Pause after each script for review |
| `--preview` | Pause after segment 1 for approval |
| `--yes` | Auto-accept all Director questions (unattended) |
| `--topics-file` | Batch mode: one topic per line |
| `--words-per-segment N` | Lock words per segment |
| `--images-per-segment N` | Lock images per segment |
| `--segment-count N` | Lock total segments |
| `--eval-models` | Run model eval harness only |

---

## 5. Studio TUI — Feature Complete (Phase 1+2+3)

The Textual-based terminal UI (`studio_tui.py`) is fully implemented:

### Tabs
- **Run**: Status badge, progress bar, log viewer, topic input
- **Stats**: Elapsed time, ETC, segment progress, VRAM gauge, throughput sparkline
- **Help**: Keyboard reference

### Run Options Panel (collapsible)
- Duration, Resume toggle, Skip-RVC, Director mode, Preview mode
- Project name, Story file path

### Keybindings
| Key | Action |
|-----|--------|
| Ctrl+Q / Ctrl+C | Quit (confirms if pipeline active) |
| Ctrl+L | Clear log |
| Ctrl+E | Scroll to end |
| F1/F2/F3 | Switch tabs (Help/Run/Stats) |
| F5 | Pause/resume pipeline |
| Ctrl+X | Cancel run (with confirmation) |
| Ctrl+O | Open output folder |
| Ctrl+Y | Copy output path |
| Ctrl+W | Save session log to file |
| Ctrl+K | Checkpoints screen |
| Ctrl+R | Artifacts screen |
| F6 | Preflight health checks |

### Screens (modal)
- **Checkpoints** (Ctrl+K): List/resume/clear checkpoints
- **Artifacts** (Ctrl+R): View run_manifest.json, segment meta, chapters
- **Preflight** (F6): Live health checks (FFmpeg, Disk, Ollama, TTS models)

---

## 6. Pipeline Improvements — Implementation Status

### Phase A: Low-Risk Quick Wins — ✅ ALL DONE

| # | Item | Status |
|---|------|--------|
| A1 | VRAM-free verify before SD loads (poll + gc.collect) | ✅ Done |
| A2 | Seed-map built once (removed per-frame disk scan) | ✅ Done |
| A3 | 2-pass EBU R128 loudnorm on final concatenated audio | ✅ Done |
| A4 | Preview SD steps (8 steps in dry_run/preview) | ✅ Done |
| A5 | Cache invented story (skip re-invention on same topic) | ✅ Done |
| A6 | `--yes` auto-accept flag | ✅ Done |
| A7 | Per-segment retry budget (max_segment_retries) | ✅ Done |

### Phase B: Medium Risk — ✅ ALL DONE

| # | Item | Status |
|---|------|--------|
| B1 | Centralized OllamaClient + 3-state circuit breaker | ✅ Done + wired to production |
| B2 | Per-segment degradation ledger (6 fallback sites) | ✅ Done |
| B3 | LLM world-state extraction (Devanagari-aware) | ✅ Done |
| B4 | CLIP 77-token budget (identity/style/scene split) | ✅ Done |
| B5 | Whisper tiny→base for final renders | ✅ Done |

### Phase C: High-Risk Staged Loop — 🟡 STUB ONLY

| # | Item | Status |
|---|------|--------|
| C1 | Staged loop reorder (text-phase → single evict → SD) | 🟡 Code exists behind `performance.staged_loop: false` flag. Not flipped to true yet. Requires real CUDA validation run. |

### Phase D: Robustness & Polish — ✅ ALL DONE

| # | Item | Status |
|---|------|--------|
| D1 | OOM auto-recovery ladder (6-tier) | ✅ Done |
| D2 | Audio crossfade between segments | ✅ Done |
| D3 | Thumbnail generation (hero frame → thumbnail.png) | ✅ Done |
| D4 | Batch mode (`--topics-file`) | ✅ Done |
| D5 | Music auto-ducking (sidechaincompress) | ✅ Done |

### Writer/TTS/Video Refinement — ✅ DONE

| # | Item | Status |
|---|------|--------|
| W1 | Per-role max_tokens (writer 1024, director 2048) | ✅ Done |
| W2 | Structured JSON writer output via OllamaClient | ✅ Done |
| W3 | Hardened _sanitize_narration (HTML/meta-commentary) | ✅ Done |
| W4 | Local sentence-trim replaces LLM word-count rewrites | ✅ Done |
| T1 | F5-TTS engine integration (optional, behind config) | ✅ Done |
| V1 | FramePack image-to-video (optional, behind config) | ✅ Done |

---

## 7. OllamaClient & Circuit Breaker (B1)

A centralized HTTP client (`utils/ollama_client.py`) now handles all Ollama calls:

- **3-state circuit breaker**: Closed → Open (fail-fast) → Half-Open (probe)
- **Singleton per config**: `get_ollama_client(config)` returns shared instance
- **Production callers wired**: `director_agent._call_ollama`, `_call_ollama_chat`, `audio_proxy.translate_hinglish`
- **NOT wired**: CrewAI agent kickoffs (they go through litellm, separate path) and `specialized_models._call_ollama` (has its own retry)

---

## 8. TTS Engine Status

| Engine | Status | Notes |
|--------|--------|-------|
| **OmniVoice** | ✅ Primary (default) | Best Hindi voice clone. Persistent worker. ~50s/chunk. |
| **F5-TTS** | ✅ Available (optional) | SPRINGLab Hindi model. Gurgly clone quality. Set `tts.engine: f5` to use. |
| **edge-tts** | ✅ Fallback | Microsoft cloud TTS. Fast but no clone. Auto-fallback if others fail. |

**Critical fix applied:** Both OmniVoice and F5 workers have a `torchaudio.load → soundfile` monkeypatch because PyTorch 2.11 broke `torchaudio.load` on Windows (torchcodec DLL missing). The `ref_text` config key MUST be set to skip Whisper ASR (which also crashes on torchcodec).

---

## 9. Known Performance Characteristics

Measured on RTX 4050 6GB, topic "A ghost ship appears in the fog", 3 segments:

| Stage | Time per Segment |
|-------|-----------------|
| Story invention | ~90s (cached on repeat) |
| Vision/analysis | ~240s (can timeout + retry) |
| Script writing | ~4-5 min (was 10-18 min before W1/W2 fixes) |
| Translation | ~25s |
| OmniVoice TTS | ~9 min (11 chunks × ~50s) |
| Stable Diffusion (8 images) | ~35s |
| Audio mastering | ~60s |
| Segment render | ~30s |
| **Total (~10 min video, 3 segs)** | **~45-60 min** |

**Biggest time sink:** OmniVoice TTS (~9 min/segment). The writer death spiral (10-18 min) was fixed by W1/W2.

---

## 10. Recent Bug Fixes (June 2026)

| Bug | Fix |
|-----|-----|
| `bootstrap_pipeline.py` patched wrong class (`Win32Console` → `LegacyWindowsTerm`) | Fixed: now patches both `write_text` and `write_styled` on the real class |
| `studio_tui.py` 4 Pyrefly type errors | Fixed: cast for `_resume_from_checkpoint`, `dict[str, Any]` annotation, `async def action_quit` |
| `num_retries=0` crashed every LLM call (TypeError) | Fixed: removed from `_create_ollama_llm`, use `OPENAI_MAX_RETRIES=0` env instead |
| Ollama model aliases not created (404 on every call) | Fixed: created hermes-director, zephyr-writer, cra-guided-7b from local GGUFs |
| TUI "crashes on topic" (actually pausing at Director questions) | Fixed: set `UIState.auto_accept = not self._opt_director` |
| Hyperframes hung 17 min/segment on Windows | Fixed: made opt-in via `VIDEOAI_USE_HYPERFRAMES=1` env |
| `config_schema.py` memory typed as `Dict[str, str]` (rejected booleans) | Fixed: changed to `Dict[str, Any]` |
| `consult_user()` returned `{}` (dict) from a `-> str` function | Fixed: returns first option or "Proceed as planned." |
| `checkpoint.py` operator-precedence bug in sibling cleanup | Fixed: explicit guard + parenthesized condition |
| `OllamaClient` missing `import urllib.error` | Fixed: added import |
| C1 staged_loop was a fake no-op | Fixed: rewrote with real batched execution |

---

## 11. Test Suite

```powershell
venv\Scripts\python.exe -m pytest tests/ -v
# Result: 234 tests pass, 0 failures
```

### Test Coverage Areas
- UIState (set_progress, reset_run, format helpers)
- Checkpoint manager (save/load/clear/atomic writes)
- Decision engine + blackboard
- Project store (3-tier memory)
- VRAM eviction polling
- Seed resolution
- Assembler loudnorm
- Story cache
- Auto-accept flag
- OllamaClient + circuit breaker (including live wiring)
- Degradation ledger
- World-state extraction
- Token budget
- OOM ladder
- Audio crossfade
- Thumbnail generation
- Batch mode
- Music ducking
- Staged loop
- LLM factory (max_tokens per role)
- Writer structured output
- Sanitize meta-commentary
- Word trim (local sentence boundary)
- TTS engine selection
- Motion engine (FramePack)

### Integration Tests (manual, not in pytest)
- `tests/manual_integration_test.py` — 12 features against live Ollama (default config)
- `tests/manual_integration_test_b.py` — 10 features with alternate config (flipped flags)

---

## 12. Architecture Overview

```
CLI/TUI/Dashboard
       │
       ▼
bootstrap_pipeline.py (patches, CLI args)
       │
       ▼
core/pipeline_long.py (run_long_pipeline)
       │
       ├── Pre-production: DirectorAgent (research, analysis, consultation)
       │                    DecisionEngine (authority model)
       │                    Blackboard (shared state)
       │
       ├── Per-segment loop (serial):
       │   ├── CrewAI Writer (structured JSON via OllamaClient)
       │   ├── Script review + local word-trim
       │   ├── Devanagari translation (sarvam-translate via OllamaClient)
       │   ├── Emotion injection
       │   ├── VRAM eviction (verified free before next stage)
       │   ├── OmniVoice TTS (persistent worker)
       │   ├── Audio mastering + SFX
       │   ├── VRAM eviction again
       │   ├── Stable Diffusion (6-tier OOM ladder)
       │   ├── Segment render (Ken Burns + subtitles)
       │   ├── Checkpoint
       │   └── Memory update (StoryMemory + WorldState)
       │
       └── Post-production:
           ├── Concatenate segments (+ loudnorm + crossfade + ducking)
           ├── Thumbnail generation
           ├── Quality check
           └── Write manifest + chapters
```

### Two Independent LLM Paths

1. **OllamaClient path** (B1 circuit breaker protected):
   - `director_agent._call_ollama` / `_call_ollama_chat`
   - `audio_proxy.translate_hinglish`
   - Writer structured output (W2)

2. **CrewAI/litellm path** (NOT circuit-breaker protected):
   - Director crew kickoff (outline planning)
   - Writer crew kickoff (when structured path fails)
   - Reviewer crew
   - Executive agent

---

## 13. Configuration Quick Reference

Primary config: `config/config.yaml`

### Key Settings
```yaml
models:
  director: "hermes-director"
  writer: "zephyr-writer"
  writer_scratch: "cra-guided-7b"
  writer_max_tokens: 1024        # W1: prevents timeout-retry spiral
  director_max_tokens: 2048

tts:
  engine: "omnivoice"            # or "f5" or "edge"
  omnivoice: {speed: 0.85, num_step: 24, ref_text: "..."}

image_gen:
  steps: 12
  height: 432, width: 768
  lock_seed: true
  preview_steps: 8
  oom_recovery: true
  token_budget: {identity: 25, style: 20, scene: 32}

video:
  motion_engine: "none"          # or "framepack"
  audio_crossfade_ms: 200
  generate_thumbnail: true

performance:
  max_workers: 1
  staged_loop: false             # C1: not yet enabled
  vram_evict_wait_s: 15
  vram_sd_threshold_gb: 4.5
  max_segment_retries: 2

script:
  writer_max_tokens: 1024
  llm_word_fix: false            # local trim instead of LLM rewrite

ollama:
  request_timeout: 240
  breaker_fails: 3
  breaker_cooldown_s: 30
```

---

## 14. Specs & Plans

Located in `.kiro/specs/`:

| Spec | Status |
|------|--------|
| `studio-tui-enhancements/` | ✅ Complete (Phase 1+2+3 implemented) |
| `pipeline-improvements/` | ✅ Phase A/B/D done, C1 stub only |
| `writer-tts-video-refinement/` | ✅ W1-W4, T1, V1 implemented |
| `model-consolidation-switch-reduction/` | 📋 Requirements written, not implemented |
| `director-decision-authority/` | ✅ Complete |
| `output-quality-fixes/` | ✅ Complete |
| `production-quality-fixes/` | ✅ Complete |

---

## 15. What's NOT Done / Remaining Work

### High Priority
1. **C1 Staged Loop** — Code exists behind `performance.staged_loop: false`. Needs a real CUDA validation run to flip the flag. Would reduce model-switch overhead significantly.
2. **Model Consolidation** — Spec written (`.kiro/specs/model-consolidation-switch-reduction/`). Goal: use ONE 7-8B model for all text roles (Director/Writer/Reviewer/Image_Engineer) switching via system prompt. Would eliminate most Ollama model switches.

### Medium Priority
3. **OmniVoice speed** — Still ~50s/chunk × 11 chunks = ~9 min/segment. Reducing `num_step` below 24 degrades quality. Alternative: shorter scripts (fewer words = fewer chunks).
4. **CrewAI path not circuit-breaker protected** — The heavy writer/director crew kickoffs go through litellm, not OllamaClient. A timeout still causes silent retries.
5. **`specialized_models._call_ollama`** — Has its own urllib loop, not wired to B1 client. Low priority (only used for image-engineer which degrades gracefully).

### Low Priority / Optional
6. **FramePack image-to-video** — Infrastructure in place (`video.motion_engine: framepack`), but FramePack model not downloaded. Would replace Ken Burns with real motion.
7. **F5-TTS** — Working but clone quality is poor for Hindi (gurgly). OmniVoice is better. Keep as English-only option.
8. **script-reviewer / image-engineer models** — ✅ Both exist in Ollama (1.9GB and 4.7GB respectively). Pipeline should be using them.
9. **Hyperframes renderer** — Now opt-in (`VIDEOAI_USE_HYPERFRAMES=1`). WSL+npx dependency is fragile. FFmpeg assembler is the reliable default.

---

## 16. File Map (Key Files)

### Entry Points
| File | Purpose |
|------|---------|
| `bootstrap_pipeline.py` | **Primary CLI entry** — patches, args, calls pipeline |
| `TUI.bat` | Double-click launcher for Studio TUI |
| `studio_tui.py` | Textual terminal UI (Phase 1+2+3) |
| `utils/local_ui.py` | FastAPI server (dashboard backend) |

### Core Pipeline
| File | Purpose |
|------|---------|
| `core/pipeline_long.py` | Main orchestration (`run_long_pipeline`) |
| `core/main.py` | CrewAI agent factory |
| `agents/director_agent.py` | DirectorAgent + UIState shared state |
| `agents/decision_engine.py` | DecisionRecord authority model |

### Audio
| File | Purpose |
|------|---------|
| `audio/audio_proxy.py` | TTS proxy, translation, engine selection |
| `audio/omnivoice_worker.py` | Persistent OmniVoice TTS worker |
| `audio/f5_worker.py` | F5-TTS worker (optional) |
| `audio/audio_fx.py` | SFX mixing, mastering |

### Video
| File | Purpose |
|------|---------|
| `video/image_gen/image_gen.py` | Stable Diffusion (OOM ladder, LoRA, seed) |
| `video/image_gen/framepack_i2v.py` | FramePack image-to-video (optional) |
| `video/renderer/assembler.py` | FFmpeg Ken Burns + concat + loudnorm + ducking |
| `video/renderer/renderer.py` | Render dispatcher (Hyperframes opt-in / assembler) |

### Utils
| File | Purpose |
|------|---------|
| `utils/ollama_client.py` | Centralized Ollama HTTP + circuit breaker |
| `utils/checkpoint.py` | Resume checkpoints |
| `utils/scene_director.py` | Prompt assembly (CLIP-safe token budget) |
| `utils/specialized_models.py` | Fast review, image prompt, world-state extraction |
| `utils/story_planner.py` | Story planning helpers |
| `utils/concurrency.py` | WorkloadScheduler, crewai_lock |

### Config
| File | Purpose |
|------|---------|
| `config/config.yaml` | All tunables (models, TTS, video, performance) |
| `config/config.py` | `load_config()`, deep-merge, project overrides |
| `config/config_schema.py` | Pydantic validation (VideoAIConfig) |
| `config/config_schemas.py` | DecisionRecord, VisionDocument, ConfigOverlay |

---

## 17. Conventions (Quick Reference)

- **Always run through bootstrap** — never import pipeline modules directly
- **Config-driven** — read from `config.get("section", {}).get("key", default)`
- **UIState changes are additive** — safe defaults, never break existing readers
- **Optional imports** — `try/except ImportError` with `log.warning` + fallback
- **Thread safety** — GPU work behind `global_scheduler`, LLM behind `crewai_lock`
- **Atomic writes** — temp file + replace for checkpoints/config
- **Paths** — always `pathlib.Path`, never string concatenation
- **Logging** — `log = logging.getLogger(__name__)` in library code, `print` only in CLI banners
- **New features need config keys** — with defaults, behind flags when risky

---

## 18. Quick Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "No segments generated" | Ollama model alias doesn't exist | `ollama create hermes-director -f Modelfile.hermes-director` |
| TUI freezes on topic | Director questions waiting for reply | Ensure Director switch is OFF (auto-accept) |
| TTS crashes with torchcodec error | PyTorch 2.11 broke torchaudio.load | Set `tts.omnivoice.ref_text` in config.yaml |
| VRAM full, 0% util deadlock | Ollama didn't fully unload | Increase `vram_evict_wait_s` or restart Ollama |
| Writer takes 10+ minutes | max_tokens too high or model rambling | Verify `script.writer_max_tokens: 1024` in config |
| Hyperframes hangs 17 min | WSL+npx Chrome render stuck | Don't set `VIDEOAI_USE_HYPERFRAMES=1` (default is FFmpeg) |
| Config validation fallback spam | Schema type mismatch | Check `memory:` is `Dict[str, Any]` in config_schema.py |
| Electron apps won't open (Antigravity/Windsurf) | AMD+NVIDIA GPU context failure | Launch with `--disable-gpu --disable-gpu-sandbox --no-sandbox` |

---

*This document supersedes the May 2026 `AI_PROJECT_REFERENCE.md` for current status. That file remains valid for architecture/onboarding reference.*
