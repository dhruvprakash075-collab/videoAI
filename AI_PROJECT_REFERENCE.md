# Video.AI — Complete Project Reference for AI/Developer Onboarding

> **Last Updated:** June 2026 (post-refactor)
> **Purpose:** Everything an AI or new developer needs to know before working on this codebase.
> **Authoritative open-bug list:** `BUGS_AUDIT_2026-05.md` — **0 open entries**
> as of 2026-06-01 (all 78 from the original 2026-05 audit are now fixed; the
> doc is now a "Resolution history" reference). The older `BUGS.md` is
> historical; superseded by the audit.

---

## 1. What Is Video.AI?

A **Dynamic Narrative Video-Generation Engine** that turns a topic or story file into a fully narrated, subtitled, multi-segment video — running entirely on local hardware (tuned for an RTX 4050 6GB laptop GPU).

**Audience:** Single operator on their own Windows machine. Not multi-tenant or publicly hosted.

### What It Does (End-to-End)

1. Plans the story arc and pacing (Director agent via Ollama LLM)
2. Writes per-segment scripts (Writer agent) and reviews them (Reviewer model)
3. Translates narration to Hindi/Devanagari when configured
4. Generates voice-over audio (OmniVoice TTS), optional voice conversion (RVC), SFX/music mixing
5. Generates per-scene imagery with Stable Diffusion, enforcing character/visual continuity
6. Renders segments with Ken Burns effect + subtitles, concatenates into final MP4

---

## 2. Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Language | Python 3.10–3.13 (NOT 3.14), Node.js (dashboard) |
| Agent Orchestration | CrewAI 1.14+ |
| LLM Serving | Ollama (localhost:11434) |
| Image Generation | Stable Diffusion via `diffusers` (Lykon/AnyLoRA, float16) |
| TTS | OmniVoice (default, voice cloning), edge-tts (fallback) |
| ASR | faster-whisper (preferred), openai-whisper (fallback) |
| Audio Processing | pydub, soundfile, FFmpeg |
| Video Rendering | FFmpeg (h264_nvenc on NVIDIA) |
| Translation | Ollama (sarvam-translate model) |
| Web Framework | FastAPI + uvicorn (localhost:8000) |
| Frontend | React 19 + Vite 8 + Tailwind CSS 4 |
| Config | PyYAML, Pydantic validation |
| GPU | PyTorch + CUDA 12.8 (6GB VRAM constraint) |

---

## 3. Critical Constraints

### 6GB VRAM Single-Model Rule
- Only ONE model fits in VRAM at a time
- Ollama models are force-evicted (keep_alive=0) before GPU tasks (SD, TTS)
- `global_scheduler` enforces max 1 concurrent HEAVY (GPU) task
- `max_workers: 1` — segments process serially

### Windows-First
- Always use Windows-friendly commands (CMD/PowerShell)
- `xformers`/`triton`/`torch.compile` unavailable — code guards against them
- Paths use `pathlib.Path`, never POSIX assumptions
- Console encoding forced to UTF-8 via bootstrap

### Always Run Through Bootstrap
```powershell
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Topic" --duration 10
```
Never run pipeline modules directly — bootstrap applies critical patches.

---

## 4. Directory Structure

```
Video.AI/
├── AGENTS.md                ← Quick orientation for AI sessions (post-refactor state)
├── bootstrap_pipeline.py    ← PRIMARY ENTRY POINT (applies patches, parses CLI)
├── run.bat                  ← Windows launcher (checks Ollama, offers UI/CLI mode)
├── auto_start.ps1           ← Non-interactive pipeline start
├── config/
│   ├── config.yaml          ← Primary configuration (ALL tunables live here)
│   ├── config.py            ← load_config(), _safe_filename(), get_character()
│   ├── config_schema.py     ← Pydantic validation models (VideoAIConfig)
│   └── config_schemas.py    ← DecisionRecord, VisionDocument, ConfigOverlay schemas
├── core/
│   ├── main.py              ← CrewAI agent factory (create_director/writer/executive)
│   ├── pipeline_long.py     ← Thin orchestrator + re-exports (644 lines, was 2830 — see §24)
│   ├── pre_production.py    ← Director phase: research, analysis, consultation, outline, LoRA
│   ├── segment_runner.py    ← Per-segment loop, approval gates, retry budget
│   └── post_production.py   ← Concat, thumbnail, chapters, manifest, QC
├── agents/
│   ├── director_agent.py    ← DirectorAgent + UIState (2619 lines — still god module, see §24)
│   ├── executive_agent.py   ← ExecutiveAgent (effectively dead — no callers, see §24)
│   └── decision_engine.py   ← DecisionRecord builder (authority model)
├── audio/
│   ├── audio_proxy.py       ← TTS generation proxy (OmniVoice/edge-tts), translation
│   ├── audio_fx.py          ← SFX mixing + audio mastering
│   └── omnivoice_worker.py  ← Persistent OmniVoice TTS worker process
├── video/
│   ├── image_gen/
│   │   └── image_gen.py     ← Stable Diffusion (3-tier OOM, LoRA, caching)
│   └── renderer/
│       ├── renderer.py      ← Hyperframes + assembler fallback
│       └── assembler.py     ← FFmpeg Ken Burns + subtitle burn-in + concat
├── memory/
│   ├── memory.py            ← StoryMemory + WorldState
│   ├── project_store.py     ← 3-tier: ProjectStore, StoryStore, PermanentMemoryLog
│   ├── blackboard.py        ← Shared DecisionRecord workspace
│   └── permanent_memory.py  ← Re-export shim
├── _archive/                ← Moved-but-not-deleted (see _archive/README.md)
│   ├── README.md            ←   documents each item + how to recover
│   ├── tts_audiobook/       ←   sibling project (219 files)
│   ├── pipeline_env/        ←   unused venv (180MB)
│   └── rvc_env/             ←   opt-in RVC venv (680MB)
├── utils/
│   ├── compatibility.py     ← Windows/encoding patches
│   ├── concurrency.py       ← WorkloadScheduler (HEAVY/LIGHT), crewai_lock
│   ├── crewai_breaker.py    ← Circuit-breaker wrapper for CrewAI kickoff() (Task 2)
│   ├── checkpoint.py        ← CheckpointManager (resume support)
│   ├── story_planner.py     ← plan_story() — uses guarded_crewai_kickoff
│   ├── scene_director.py    ← enrich_prompts(), assemble_prompt() (CLIP-safe)
│   ├── emotion_control.py   ← inject_emotion(), get_mood_rate()
│   ├── specialized_models.py← review_script_fast(), generate_image_prompt() (NOT yet on B1 breaker)
│   ├── context_manager.py   ← _llm_compress() — uses guarded_crewai_kickoff
│   ├── retry_manager.py     ← Retry + backoff (transient vs bounded)
│   ├── local_ui.py          ← FastAPI backend for dashboard
│   └── __init__.py          ← Re-exports (load_config, build_prompts, etc.)
├── dashboard/               ← React 19 + Vite frontend
│   └── src/components/      ← ControlPanel, StatusTracker, VoiceManager, ABPlayground
├── projects/                ← Per-series config overrides (e.g. series_1.yaml)
├── prompts.yaml             ← All LLM prompt templates
├── styles.yaml              ← Visual style presets (3-layer resolver)
├── style_resolver.py        ← StyleResolver class
├── train_lora.py            ← LoRA face-lock training
├── requirements.txt         ← Python dependencies
├── BUGS.md                  ← Historical B1–B40 catalog
├── BUGS_AUDIT_2026-05.md    ← Authoritative P0–P5 bug catalog (74+ verified fixed)
├── PROJECT_STATUS.md        ← Hardware, architecture, current test coverage
└── FUTURE_ROADMAP.md        ← Tiered feature roadmap (TTS speed, FramePack, ESRGAN, etc.)
```

### Generated/Runtime Directories (NOT source code)
```
studio_outputs/          Final rendered videos + manifests
studio_checkpoints/      Resume checkpoints, story_memory, world_state, blackboard
studio_projects/         Three-tier memory (ProjectStore/StoryStore)
cache/                   Vision cache
logs/                    Run logs
temp_srt_files/          Temporary subtitle files (cleaned up)
character_voices/        Reference voice samples for TTS cloning
venv/                    Python virtualenv
ffmpeg-8.1.1-essentials_build/   Bundled FFmpeg binaries
```

---

## 5. Pipeline Flow (Detailed)

### Phase A: Pre-Production (`run_pre_production()`)

```
CLI args → load_config() → DirectorAgent
  ├── Phase 0: Ask user (search online? create from scratch? cache TTL?)
  │            Select writer model (creative vs faithful)
  ├── Phase 1: Web research (Wikipedia + DuckDuckGo) [optional]
  ├── Phase 2: Director analyzes story → VisionDocument JSON
  │            (characters, style, pacing, emotions, duration recommendation)
  ├── Phase 2.5: Duration negotiation
  │            (cliffhanger detection / story compaction / custom)
  ├── Phase 3: User consultation (CLI multi-choice or web UI)
  │            (visual style, subtitles, TTS engine, custom instructions)
  ├── Phase 4: Writer collaboration (segment_count, words/seg, pacing_notes)
  └── Phase 5: Build config overlay + DecisionRecord → persist to Blackboard
```

### Phase B: Production (`run_long_pipeline()` continued)

```
Config overlay merged → Preflight checks → Patch retries
  → Seed Director knowledge into memory stores
  → Studio Session (pre-train Face-Lock LoRAs)
  → Plan story outline (CrewAI, batched for >25 segments)
  → Per-segment loop (serial):
      ├── Build context (ContextWindowManager + WorldState + StoryMemory)
      ├── Write script (CrewAI Writer + review + word-count enforcement)
      ├── Director Mode approval gate [optional]
      ├── Translate to Devanagari (Ollama sarvam-translate)
      ├── Inject emotion markers
      ├── Evict Ollama models from VRAM
      ├── TTS generation (OmniVoice persistent worker)
      ├── Audio mastering + SFX mixing
      ├── Evict Ollama models again
      ├── Generate images (Stable Diffusion + LoRA + 3-tier OOM)
      ├── Render segment MP4 (Ken Burns + subtitles)
      ├── Checkpoint state
      ├── Preview gate after segment 1 [optional]
      └── Update WorldState + StoryMemory
  → Concatenate segments → Final video
  → Quality check → Write manifest + chapters
```

---

## 6. Configuration Deep Dive

### config/config.yaml (Key Sections)

```yaml
models:
  director: "hermes-director"        # Story planning (Llama 8B Q4_K_S)
  writer: "zephyr-writer"            # Default writer (Llama 7B Q4_K_M)
  writer_scratch: "cra-guided-7b"    # Invention mode (Qwen2 7.6B)
  writer_adapt: "zephyr-writer"      # Adaptation mode
  reviewer: "script-reviewer"        # Fast review (Qwen2 3B) — graceful-degrade
  image_engineer: "image-engineer"   # Image prompt generation (7B) — graceful-degrade
  translator: "sarvam-translate"     # Hindi/Devanagari (Gemma3 3.9B Q4_K_M)

ollama:
  host: "http://localhost:11434"
  keep_alive: "3m"
  request_timeout: 240               # Hard per-request cap (seconds)

tts:
  engine: "omnivoice"                # or "edge"
  lang: "hi"
  omnivoice: {speed: 0.85, num_step: 24, guidance_scale: 2.5}

image_gen:
  sd_model_path: "Lykon/AnyLoRA"
  height: 432, width: 768            # Native gen resolution (upscaled to 1080p)
  steps: 12                          # DPM++ 2M sweet spot
  guidance_scale: 6.0

video:
  total_duration_min: 10
  segment_duration_min: 2
  fps: 24
  encoder: "h264_nvenc"
  video_bitrate: "8M"

performance:
  max_workers: 1                     # SERIAL — 6GB GPU can't parallelize
```

### Per-Project Overrides

Place `projects/{name}.yaml` to override any config key for a series:
```yaml
project_name: "Series 1 Template"
narrator_persona: "a gritty, world-weary Victorian detective"
visual:
  style: "Gothic Horror, Dark Victorian, Volumetric Lighting"
characters:
  protagonist:
    name: "Custom Character"
    description: "Detailed visual description..."
```

---

## 7. Agent Architecture

### Authority Model (Decision Engine)
```
default < director < writer < user / cli_flag
```
Only user/cli_flag can LOCK a field. Locked fields cannot be overridden by lower authority.

### CrewAI Agents (core/main.py)

| Agent | Model | Role |
|-------|-------|------|
| Director | hermes-director (8B) | Story planning, outline, JSON structure |
| Writer | zephyr-writer (default) / cra-guided-7b (scratch) | Script generation, revision |
| Executive | Same as Writer | Production management, continuity audit |
| Reviewer | script-reviewer (3B) | Fast script quality review (graceful-degrade) |
| Image Engineer | image-engineer (7B) | Detailed SD prompt generation (graceful-degrade) |
| Translator | sarvam-translate | English → Devanagari |

### Key Serialization Rules
- `crewai_lock`: ALL CrewAI `kickoff()` calls serialize through this lock
- `_translation_lock`: Director translation calls serialize to prevent model overlap
- `global_scheduler.task("heavy", ...)`: Only 1 GPU task at a time

---

## 8. Memory & Continuity System

### Three-Tier Architecture

```
studio_projects/
  {project}/
    project.json          ← ProjectStore (shared: characters, world lore, visual locks)
    stories/
      {story}/
        story.json        ← StoryStore (per-story: segments, arc)
        audit.json        ← Continuity audit log
```

### Memory Types

| Type | Scope | Purpose |
|------|-------|---------|
| StoryMemory | Per-topic | Last 3 segment summaries for Writer context |
| WorldState | Per-topic | Hard constraints (facts, characters, threads) injected into prompts |
| ProjectStore | Per-project | Shared characters, visual locks, world lore across stories |
| StoryStore | Per-story | Segment scripts/summaries, continuity audit |
| Blackboard | Per-run | DecisionRecord (single source of truth for structural decisions) |
| PermanentMemoryLog | Compat shim | Routes to ProjectStore/StoryStore |

### WorldState Prompt Injection
```
[World State - Hard Constraints for this segment]
Established facts (do NOT contradict):
  • The Fog cannot be dispelled by ordinary means
Open plot threads (you may develop but not arbitrarily resolve):
  ? Who is the mysterious figure watching from the shadows?
Active characters: Protagonist, Mentor, Guardian
[/World State]
```

---

## 9. GPU Memory Management

### VRAM Budget (6GB RTX 4050)
- Ollama LLM: ~5GB (one model at a time)
- Stable Diffusion: ~4GB (float16 + attention slicing + VAE tiling)
- OmniVoice TTS: ~4GB
- **Never co-resident** — explicit eviction between stages

### Eviction Flow
```python
_evict_ollama_models(config, reason="StableDiffusion")  # keep_alive=0 for all models
torch.cuda.empty_cache()                                 # Free PyTorch cache
# Now safe to load SD pipeline
```

### OOM Recovery (Image Generation)
```
Tier 1: Normal CUDA inference (12 steps)
  ↓ OOM
Tier 2: Clear cache + reduced steps (60% of normal)
  ↓ OOM
Tier 3: CPU fallback (4 steps, lower quality)
  ↓ All failed
Skip image (logged in OOM report)
```

---

## 10. Audio Pipeline

### TTS Flow
```
Script → inject_emotion(mood) → translate_to_devanagari() → tts_generate()
  → OmniVoice persistent worker (or edge-tts fallback)
  → RVC voice conversion [optional, disabled by default]
  → mix_sfx() [keyword-matched SFX overlay]
  → master_audio() [compression, normalization, de-essing]
  → Final WAV
```

### OmniVoice Persistent Worker (B16 fix)
- Spawned once, model stays loaded across all segments
- Communicates via line-delimited JSON on stdin/stdout
- Splits long text into ~500-char sentence-bounded chunks
- Creates voice clone prompt once from reference audio (8s mono)
- Progress reporting prevents idle timeout kills

### Emotion Control
- `inject_emotion(script, mood, lang)`: Adds mood-appropriate punctuation
  - mysterious/horror: ellipses, slow pauses
  - action: exclamation marks, dashes
  - intimate: softened punctuation
- `get_mood_rate(mood)`: Speed multiplier (0.85–1.1) passed to TTS

---

## 11. Image Generation Pipeline

### Flow
```
Script → build_prompts() → enrich_prompts() [camera + style + neg prompt]
  → generate_image_prompt() [image-engineer LLM]
  → Character visual lock injection (prepend descriptions for CLIP survival)
  → Per-frame LoRA adapter activation (based on char_presence weights)
  → Stable Diffusion inference (per-character seed for consistency)
  → Optional upscale (Lanczos or Real-ESRGAN)
  → Cache by prompt hash
```

### Character Consistency Mechanisms
1. **Visual Lock**: Character descriptions PREPENDED to prompts (survives CLIP 77-token truncation)
2. **LoRA Face-Lock**: Pre-trained per-character LoRA adapters, activated per-frame
3. **Fixed Seed**: Per-character deterministic seed from visual lock or hash
4. **char_presence weights**: Per-frame dict mapping character IDs to visual weight (0.0–1.0)
   - < 0.3: environment shot (character not visible)
   - 0.3–0.7: balanced (character + environment)
   - > 0.7: character-dominant portrait

### Prompt Assembly (CLIP-Safe)
```python
assemble_prompt(
    identity_tokens="young adult, warm brown eyes, short black hair...",  # FIRST (highest priority)
    scene_tokens="walking through fog, mysterious atmosphere...",
    style_tokens="semi-realistic 2D, cinematic lighting...",             # LAST (trimmed first)
    budget=70  # ~77 CLIP tokens minus headroom
)
```

---

## 12. Video Rendering

### Segment Assembly (assembler.py)
- Ken Burns zoompan effect on each image (slow zoom-in)
- Images distributed evenly across audio duration
- Cinematic fade-in/fade-out per segment
- Subtitle burn-in via FFmpeg `subtitles` filter
- Hardware encoding: h264_nvenc (Ada NVENC p5, spatial-aq, temporal-aq)

### Subtitle Modes
- **TikTok**: Word-level timing via Whisper ASR (one word at a time, uppercase)
- **Classic**: Sentence-proportional timing (word-count weighted blocks)

### Final Concatenation
- FFmpeg concat demuxer (copy video, re-encode audio to AAC 192k)
- Optional background music mixing (volume 0.15, fade-in 3s, duration=first)

---

## 13. Checkpoint & Resume System

### How It Works
- Per-topic JSON in `studio_checkpoints/`
- Each step saved: `{step_name: {data: ..., ts: ISO8601}}`
- On resume: checks if step exists → skips if found
- TTL expiry: 24 hours (configurable)
- Atomic writes with .bak backup

### Checkpointed Steps
- `{topic}_meta.outline`: Story outline
- `{topic}_seg{NN}.script`: Generated script
- `{topic}_seg{NN}.audio`: TTS audio path + word timestamps
- `{topic}_seg{NN}.images`: Generated image paths
- `{topic}_seg{NN}.video`: Rendered segment MP4

---

## 14. Dashboard & API

### FastAPI Backend (utils/local_ui.py) — localhost:8000

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | Pipeline state, logs, active question, output video |
| `/api/upload_script` | POST | Upload story file → start pipeline in background |
| `/api/upload_voice` | POST | Upload voice sample (auto-trimmed to 10s mono) |
| `/api/voices` | GET | List available voice samples |
| `/api/config` | GET/POST | Read/write UI configuration |
| `/api/consultation_reply` | POST | Answer Director's question → resume pipeline |
| `/api/manual_pause` | POST | Trigger manual creative pause |
| `/api/ab/generate` | POST | Start A/B image comparison job |
| `/api/ab/status/{id}` | GET | Poll A/B job status |
| `/api/ab/pick` | POST | Commit chosen A/B variant |

### React Frontend (dashboard/) — localhost:5173

- **Director Canvas**: Video preview + script upload
- **Voice Studio**: Upload/manage character voice samples
- **A/B Testing**: Compare two prompt variants visually
- **Settings Panel**: Voice engine, subtitle style, image count
- **Status Tracker**: Real-time log viewer (bottom-right floating)
- **Consultation Modal**: Human-in-the-loop creative pause

---

## 15. CLI Flags Reference

```powershell
venv\Scripts\python.exe bootstrap_pipeline.py [FLAGS]

--topic "Topic"          # Video topic/title
--file path/to/story.txt # Story file input (overrides --topic)
--duration 10            # Override total duration (minutes)
--dry-run                # Preview without generating video
--no-resume              # Start fresh (ignore checkpoints)
--skip-rvc               # Skip RVC voice conversion
--project series_1       # Load projects/series_1.yaml overrides
--series                 # Resume series without re-consultation
--director-mode          # Pause after each script for human review
--preview                # Pause after segment 1 for approval
--run-mode project|one_time  # Persist continuity or isolated run
--eval-models            # Run model eval harness (no video)
--words-per-segment 130  # Lock words per segment
--images-per-segment 6   # Lock images per segment
--segment-count 5        # Lock total segment count
```

---

## 16. Coding Conventions

### Python
- **Imports**: Pipeline modules add repo root to `sys.path` and import compatibility patches before heavy deps
- **Module headers**: One-line docstring describing role
- **Config-driven**: Read tunables from loaded config dict, never hardcode
- **Optional deps**: Wrap in `try/except ImportError` with `log.warning` and graceful fallback
- **Logging**: Use `log = logging.getLogger(__name__)`, not `print` (except CLI banners)
- **Thread safety**: GPU/LLM work behind locks/scheduler
- **Naming**: snake_case for files/functions; PascalCase for classes
- **Paths**: Always use `pathlib.Path`, never POSIX assumptions

### Dashboard (JavaScript)
- **Naming**: PascalCase `.jsx` components
- **Style**: Tailwind CSS 4 utility classes
- **Icons**: lucide-react
- **API**: Fetch to localhost:8000, poll every 1.5s

---

## 17. Common Tasks

### Add a New Ollama Model
1. Pull: `ollama pull model-name`
2. Add to `config/config.yaml` under `models:`
3. Reference in the appropriate agent factory in `core/main.py`

### Add a New TTS Engine
1. Create adapter function in `audio/audio_proxy.py`
2. Add to engine registry in `tts_generate()`
3. Add capability profile to `tts_capabilities()`
4. Add config section under `tts:` in config.yaml

### Add a New Visual Style
1. Add entry to `styles.yaml` with keywords, aliases, and SD prompt
2. StyleResolver will auto-detect via keyword/fuzzy matching

### Add a New Character
1. Add to `config/config.yaml` under `characters:`
2. Include: name, description (50+ words with specific visual details), keywords
3. Optionally add voice sample to `character_voices/`

### Add a New SFX
1. Drop WAV file in `sfx/` directory
2. Add keyword mapping in `audio/audio_fx.py` `_DEFAULT_SFX` dict

---

## 18. Error Handling Patterns

### Retry Strategy
| Error Type | Max Retries | Backoff | Applied To |
|-----------|-------------|---------|-----------|
| Transient (network/timeout) | 50 | Exponential (3s base, 1.5x, max 60s) | tts_generate, translate |
| Bounded (RuntimeError/OSError) | 3 | Same | General |
| Ollama calls | 3 | 2^attempt seconds | All Director/Writer calls |
| Image OOM | 3 tiers | Immediate | Internal to generate_images |

### Graceful Degradation
- TTS fails → silent audio fallback (FFmpeg-generated silence matching segment duration)
- Image gen fails → black frames
- Hyperframes fails → FFmpeg assembler fallback
- Writer model not pulled → falls back to Director model
- OmniVoice persistent worker fails → one-shot subprocess fallback
- Whisper faster-whisper fails → openai-whisper fallback
- Real-ESRGAN unavailable → Lanczos resize fallback

---

## 19. Known Issues

**See `BUGS_AUDIT_2026-05.md`** for the authoritative list of currently open
bugs (13 entries as of 2026-06-01). That doc is the single source of truth —
do not duplicate it here.

The historical `BUGS.md` (B1–B40) catalog is retained as a reference; almost
all of its entries are now `✅ FIXED` (notably B1/B2/B4/B5/B7–B10/B13–B16/B19/B20
and B23–B27) and are summarized in the "Resolution history" section of
`BUGS_AUDIT_2026-05.md`.

---

## 20. Testing

### Run Tests
```powershell
venv\Scripts\python.exe -m pytest tests/ -v
```

### Key Test Files
- `tests/test_project_store.py` — Memory tier tests
- `tests/test_decision_engine.py` — Authority model tests (if exists)

### Manual Testing
```powershell
# Dry run (no video, fast)
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Test" --dry-run

# Short test run
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Test" --duration 2 --no-resume

# Model eval (sample images + TTS clip)
venv\Scripts\python.exe bootstrap_pipeline.py --eval-models
```

---

## 21. Environment Setup

### Prerequisites
- Python 3.10–3.13 with virtualenv at `venv/`
- Ollama installed and running (localhost:11434)
- Required Ollama models pulled (see requirements.txt comments)
- NVIDIA GPU with CUDA 12.8 drivers
- Node.js (for dashboard)
- FFmpeg (bundled in `ffmpeg-8.1.1-essentials_build/`)

### Install
```powershell
# Python deps
pip install -r requirements.txt
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# Ollama models
ollama pull hermes-director
ollama pull zephyr-writer
ollama pull cra-guided-7b
ollama pull script-reviewer
ollama pull image-engineer
# sarvam-translate: create from local GGUF (C:\models\sarvam-translate.i1-Q6_K.gguf)

# Dashboard
cd dashboard && npm install
```

### Environment Variables
- `OLLAMA_MODELS` — Ollama model storage path (default: C:\models)
- `DIRECTOR_TIMEOUT` — Consultation timeout in seconds (0 = no timeout)
- `VIDEOAI_WSL_DISTRO` — WSL distro for Hyperframes (default: Ubuntu)
- `VIDEOAI_WSL_USER` — WSL user for Hyperframes

---

## 22. Existing Specs (.kiro/specs/)

| Spec | Purpose |
|------|---------|
| `director-decision-authority/` | DecisionRecord authority model implementation |
| `output-quality-fixes/` | P0/P1 bug fixes for output quality |
| `production-quality-fixes/` | Production reliability improvements |

---

## 23. Important Patterns to Preserve

1. **Always evict Ollama before GPU tasks** — prevents VRAM deadlock
2. **Always use `global_scheduler.task("heavy", ...)` for GPU work** — prevents concurrent OOM
3. **Always serialize CrewAI kickoff() through `crewai_lock`** — prevents executor corruption
4. **Always wrap CrewAI calls in `guarded_crewai_kickoff`** — trips the per-model breaker on hang
5. **Always checkpoint after each step** — enables resume on crash
6. **Always use atomic writes (temp + replace)** — prevents corruption on crash
7. **Always wrap optional imports in try/except** — graceful degradation
8. **Never hardcode paths** — use pathlib.Path and config-driven values
9. **Never run pipeline modules directly** — always through bootstrap
10. **Config changes go in config.yaml** — not hardcoded in Python
11. **New features need config keys** — with defaults in `_default_config()`

---

## 24. Recent Refactor (June 2026) — DO NOT REVERT

Two related refactors shipped in early June 2026. All 235 tests pass.

### 24.1 `core/pipeline_long.py` split (Task 1)

`pipeline_long.py` was 2,830 lines — a god module mixing pre-production,
per-segment execution, and post-production. Split by **phase** into four files:

| File | Lines | Role |
|------|-------|------|
| `core/pipeline_long.py` | 644 | Thin orchestrator. `run_long_pipeline()` + re-exports. |
| `core/pre_production.py` | 825 | Director research, analysis, consultation, outline, LoRA session, memory seeding. |
| `core/segment_runner.py` | 1,147 | Per-segment loop, approval gates, retry budget, `make_process_segment(...)`. |
| `core/post_production.py` | 266 | Concat, thumbnail, chapters, manifest, QC. |

**Re-exports kept** at `core.pipeline_long` for backward compat with
`bootstrap_pipeline.py`, `studio_tui.py`, and tests:
- `_sanitize_narration`, `_evict_ollama_models`, `_director_set_abort`,
  `request_cancel`, `set_director_abort_flag`, `_run_with_timeout_fallback`,
  `_load_check_or_init`, `_safe_input`, `_get_world_state_loaded`,
  `process_segment`, etc.

**Post-split fixes** (2026-06-01 audit pass — see `BUGS_AUDIT_2026-05.md` §"Post-audit fixes"):
- P5-1: `BreakerOpen.cooldown_s` now reports real remaining cooldown (was hardcoded `0.0`)
- P5-2: light scheduler slot wait `300s → 60s` in `utils/concurrency.py`
- P5-3: `process_segment` closure was being built twice in `run_long_pipeline` — fixed to build once inside the `ThreadPoolExecutor` block
- P5-4: dead `from core.pre_production import _deep_merge` import removed from `utils/crewai_breaker.py`

### 24.2 `utils/crewai_breaker.py` (Task 2)

CrewAI's litellm backend can hang for minutes on a bad generation.
Created `utils/crewai_breaker.py` wrapping `crew.kickoff()` with:

- **Hard wall-clock timeout** (default 240s, configurable)
- **Per-model circuit breaker** — reuses `OllamaClient._breaker(model)` so a
  failing model opens ONE breaker whether called via `OllamaClient.generate()`
  OR `crew.kickoff()`
- **`BreakerOpen` exception** for fast-fail; carries real remaining cooldown
- **`crewai_lock` (RLock)** to serialize concurrent kickoffs (B15 fix, RLock per P3-14)

**Wired into 3 call sites:**
- `core/segment_runner.py` — writer + revision kickoffs
- `utils/story_planner.py` — outline planner (`plan_story`)
- `utils/context_manager.py` — context compression (`_llm_compress`)

**API:**
```python
from utils.crewai_breaker import guarded_crewai_kickoff, BreakerOpen

try:
    result = guarded_crewai_kickoff(crew, model_name="zephyr-writer", timeout_s=240)
except BreakerOpen as e:
    # e.model, e.cooldown_s (real remaining), str(e) readable
    ...  # fall back to a different model
```

### 24.3 What is NOT yet done (deferred)

- **Split `agents/director_agent.py`** (2,619 lines, still a god module). Same
  pattern as Task 1 would apply: `director_state.py`, `director_ollama.py`,
  `director_crew.py`, `director_cache.py`. Deferred because higher-value items
  (framepack, model consolidation) take priority.
- **Wire `utils/specialized_models._call_ollama` to B1 breaker.** Still has its
  own urllib loop; low priority because it only feeds image-engineer which
  degrades gracefully. Trivial ~20-line fix when prioritized.
- **Validate C1 staged loop end-to-end** on a real CUDA run. Code is ready
  (`performance.staged_loop: true`) but never been confirmed on a real full run.
- **Remove dead `ExecutiveAgent`** — no callers; documented in BUGS_AUDIT as P4-21.
- **Cleanup `tts_audiobook/` docs** in `static/*.html` (cosmetic IP-leakage).
