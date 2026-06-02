# Video.AI — Future Roadmap

> **Created:** June 1, 2026  
> **Based on:** Current codebase state, measured performance, and architecture analysis.

---

## Priority Tiers

### 🔴 Tier 1 — Biggest Impact (Do These First)

These give you the most noticeable improvement for the least risk.

---

#### 1. Speed: Cut OmniVoice TTS Time in Half

**Problem:** OmniVoice is ~9 min/segment (50s × 11 chunks). It's the single biggest time sink.

**Options:**
- **Reduce script length** — Currently 130 words/segment. Drop to 80-100 words = fewer TTS chunks = proportionally faster. Trade-off: shorter narration per segment.
- **Reduce `num_step`** — Currently 24. Try 16 (saves ~33% time). Test if quality is acceptable for your voice.
- **Batch shorter chunks** — Current chunk split is ~500 chars. Larger chunks = fewer round-trips but risk OOM.
- **Switch to Kokoro-82M for English** — If you ever do English narration, Kokoro is near-realtime (360MB model, runs on CPU). Won't help Hindi though.

**Effort:** Config change (low) to code change (medium)  
**Expected gain:** 3-5 min saved per segment

---

#### 2. Speed: Enable the Staged Loop (C1)

**Problem:** Each segment does: LLM → evict → SD → evict → TTS → evict → LLM (next seg). That's 3 model switches per segment.

**What C1 does:** Batch all text work (write scripts for N segments while writer model is loaded), then ONE evict, then all SD work, then ONE evict, then all TTS. Cuts model switches from 3×N to 3 total.

**How to enable:**
```yaml
performance:
  staged_loop: true
  lookahead_segments: 3   # batch 3 segments of text before switching to SD
```

**Risk:** Medium. Code exists and is tested, but hasn't been validated on a real full run. Do a `--duration 2 --yes` test first.

**Expected gain:** 2-4 min saved per 3 segments (model load time is ~30-60s each)

---

#### 3. Quality: Reviewer & Image-Engineer Models — ✅ ALREADY DONE

Both `script-reviewer` (1.9GB) and `image-engineer` (4.7GB) are already created in Ollama. The pipeline should be using them. If scripts still feel unreviewed or SD prompts feel generic, verify the pipeline is actually calling them (check logs for "review" and "image_engineer" mentions during a run).

**Models currently installed:**
- `hermes-director` (4.4GB) — story planning
- `zephyr-writer` (4.1GB) — script writing (default + adapt)
- `cra-guided-7b` (4.4GB) — creative invention
- `sarvam-translate` (2.3GB) — Hindi translation
- `script-reviewer` (1.8GB) — script quality review ✅
- `image-engineer` (4.4GB) — detailed SD prompts ✅

---

#### 4. Quality: Model Consolidation (One Model for All Text Roles)

**Problem:** You have 4 different models for text tasks (hermes-director, zephyr-writer, cra-guided-7b, sarvam-translate). Each switch takes 30-60s to load.

**The plan (spec already written):** Use ONE good 7-8B instruct model (like Qwen2.5-7B-Instruct) for Director + Writer + Reviewer + Image-Engineer, switching roles via system prompt only. Keep sarvam-translate separate (it's specialized).

**Benefits:**
- Zero model-switch time between text stages
- Simpler setup (one GGUF instead of four)
- Combined with C1 staged loop = massive speed gain

**Effort:** Medium-high. Spec exists at `.kiro/specs/model-consolidation-switch-reduction/`. Needs implementation.

---

### 🟡 Tier 2 — Nice Upgrades (Do When Tier 1 is Stable)

---

#### 5. Visual: FramePack Image-to-Video (Real Motion) — ✅ CODE READY

**What:** Instead of Ken Burns (slow zoom on static images), generate actual 3-5 second video clips from each image. Characters move, fog drifts, etc.

**Status:** Fully wired into the pipeline (`video/image_gen/framepack_i2v.py` + hook in `pipeline_long.py`). You just need to install the package and download the model.

**How to enable:**
```powershell
venv\Scripts\pip.exe install framepack
venv\Scripts\huggingface-cli.exe download lllyasviel/FramePack --local-dir hf_cache\framepack
```
Then in config.yaml:
```yaml
video:
  motion_engine: "framepack"
  motion_seconds_per_image: 3
```

**Trade-off:** Each image takes ~2-3 min to animate (vs ~4s for Ken Burns). A 3-segment video would add ~30 min. But the visual quality jump is huge.

---

#### 6. Visual: Real-ESRGAN Upscaling — ✅ CODE READY

**What:** Currently images are upscaled from 768×432 to 1920×1080 using Lanczos (basic resize). Real-ESRGAN gives sharper, more detailed upscales.

**Status:** Code exists in `image_gen.py` (`_maybe_upscale`). Config already points to `4x-UltraSharp`. Just needs the packages + model weights.

**How:**
```powershell
pip install realesrgan basicsr
# Download 4x-UltraSharp.pth and set path in config:
```
```yaml
image_gen:
  upscaler:
    model: "4x-UltraSharp"
    model_path: "path/to/4x-UltraSharp.pth"
```

**Trade-off:** Adds ~2-3s per image + ~1GB VRAM during upscale. May need to evict SD first.

---

#### 7. Audio: Background Music with Auto-Ducking — ✅ CODE READY

**What:** Add background music that automatically gets quieter when narration plays (like a podcast).

**Status:** Fully implemented (D5 sidechaincompress in `assembler.py`). Just needs config + a music file.

```yaml
music:
  enabled: true
  ducking: true
  duck_ratio: 0.3
```
Drop a music file where the pipeline expects it (or configure a path).

**Effort:** Config + provide music files. Zero code needed.

---

#### 8. Distribution: Auto-Upload to YouTube/Social

**What:** After video is rendered, automatically upload to YouTube, generate a description, add chapters, set thumbnail.

**How:** Use the YouTube Data API v3 (OAuth2). Could also post clips to Instagram/TikTok.

**Effort:** High (new feature). Would need a new `utils/uploader.py` module + OAuth setup.

---

### 🟢 Tier 3 — Future Vision (Longer Term)

---

#### 9. Multi-Language Support

**What:** Currently Hindi/Devanagari only. Add English, Spanish, Arabic, etc.

**Needs:**
- Per-language TTS engine selection (Kokoro for English, edge-tts for others)
- Per-language subtitle font
- Translation model per target language (or skip translation for English)
- Language-aware emotion injection

**Effort:** Medium-high. Most infrastructure exists, needs wiring.

---

#### 10. Character Consistency via IP-Adapter / InstantID

**What:** Instead of relying on text prompts + LoRA for character consistency, use IP-Adapter or InstantID to inject a reference face image directly into SD generation. Much more consistent faces.

**Trade-off:** Needs ~1-2GB extra VRAM during SD. May need `model_cpu_offload: true`.

---

#### 11. Voice Acting (Multiple Characters)

**What:** Different voice for each character (not just one narrator). Detect dialogue in script, route each character's lines to a different voice clone.

**Needs:**
- Per-character voice samples in `character_voices/`
- Script parser that identifies speaker
- TTS routing per character

**Effort:** High. Significant audio_proxy changes.

---

#### 12. Interactive Story Mode (Viewer Choices)

**What:** Generate branching narratives where the viewer picks what happens next (like a choose-your-own-adventure video).

**Needs:**
- Branch points in the script
- Multiple segment variants rendered
- YouTube end-screen cards or interactive video platform

**Effort:** Very high. Architectural change to pipeline.

---

#### 13. Live Streaming / Real-Time Generation

**What:** Generate and stream video segments in near-real-time as they're produced, instead of waiting for the full video.

**Needs:**
- Streaming output (HLS/DASH)
- Segment-by-segment publishing
- Much faster generation (probably needs better GPU or cloud burst)

**Effort:** Very high.

---

#### 14. Web-Based Editor (Post-Production)

**What:** After generation, open a web UI where you can:
- Reorder segments
- Regenerate specific images you don't like
- Edit scripts and re-render just that segment
- Adjust timing/pacing

**Effort:** High. New dashboard feature + selective re-render in pipeline.

---

## Recommended Execution Order

```
Week 1:  #1 (TTS speed tweak — config only)
         #3 (Create reviewer + image-engineer models)
         
Week 2:  #2 (Enable staged loop — test with short video)
         #7 (Enable music + ducking — config only)

Week 3:  #4 (Model consolidation — the big refactor)

Week 4:  #5 (FramePack — download model, test)
         #6 (Real-ESRGAN — install, test)

Later:   #8-14 based on what direction you want to take the project
```

---

## Quick Wins You Can Do Right Now (Zero Code)

| Change | Where | Effect |
|--------|-------|--------|
| Reduce words_per_segment to 100 | config.yaml `script.words_per_segment` | Fewer TTS chunks = faster |
| Reduce OmniVoice num_step to 16 | config.yaml `tts.omnivoice.num_step` | ~33% faster TTS (test quality) |
| Enable music ducking | config.yaml `music.enabled: true` | Background music auto-ducks |
| Enable thumbnail | Already enabled ✅ | Saves hero frame as thumbnail.png |
| Try staged_loop | config.yaml `performance.staged_loop: true` | Fewer model switches |
| Increase lookahead | config.yaml `performance.lookahead_segments: 3` | Batch more text work |
| Enable LLM world-state | config.yaml `memory.llm_world_state: true` | Better continuity (Devanagari-aware) |

---

## Features Already Built & Working (Verified in Code)

| Feature | Config Key | Status |
|---------|-----------|--------|
| 2-pass loudnorm (A3) | `audio_fx.program_loudnorm: true` | ✅ In assembler.py |
| Audio crossfade (D2) | `video.audio_crossfade_ms: 200` | ✅ In assembler.py |
| Music auto-ducking (D5) | `music.enabled + ducking: true` | ✅ In assembler.py |
| Thumbnail generation (D3) | `video.generate_thumbnail: true` | ✅ In pipeline_long.py |
| Batch mode (D4) | `--topics-file` CLI flag | ✅ In bootstrap_pipeline.py |
| OOM recovery ladder (D1) | `image_gen.oom_recovery: true` | ✅ In image_gen.py |
| FramePack motion (V1) | `video.motion_engine: framepack` | ✅ Code ready (needs model download) |
| Real-ESRGAN upscale | `image_gen.upscaler.model: 4x-UltraSharp` | ✅ Code ready (needs pip install + weights) |
| Staged loop (C1) | `performance.staged_loop: true` | ✅ Code ready (needs real-run validation) |
| Circuit breaker (B1) | `ollama.breaker_fails: 3` | ✅ Live in production |
| Degradation ledger (B2) | Automatic (6 fallback sites) | ✅ + TUI badge shows count |
| LLM world-state (B3) | `memory.llm_world_state: true` | ✅ In memory.py + specialized_models.py |
| Token budget (B4) | `image_gen.token_budget.*` | ✅ In scene_director.py |
| Whisper base for finals (B5) | `performance.whisper_model_final: base` | ✅ In pipeline_long.py |
| Story cache (A5) | `cache.cache_invented_story: true` | ✅ In director_agent.py |
| Auto-accept (A6) | `--yes` CLI flag | ✅ In bootstrap + TUI |
| Retry budget (A7) | `performance.max_segment_retries: 2` | ✅ In pipeline_long.py |
| VRAM verify (A1) | `performance.vram_evict_wait_s: 15` | ✅ In pipeline_long.py |
| Seed lock (A2) | `image_gen.lock_seed: true` | ✅ In image_gen.py |
| Preview steps (A4) | `image_gen.preview_steps: 8` | ✅ In pipeline_long.py |
| Structured writer (W2) | Automatic (JSON via OllamaClient) | ✅ In pipeline_long.py |
| Local word-trim (W4) | `script.llm_word_fix: false` | ✅ In pipeline_long.py |
| F5-TTS (T1) | `tts.engine: f5` | ✅ Code ready (OmniVoice preferred for Hindi) |
| script-reviewer model | config `models.reviewer` | ✅ Created in Ollama |
| image-engineer model | config `models.image_engineer` | ✅ Created in Ollama |

---

*This roadmap is based on the current state as of June 2026. Priorities may shift based on what you're producing and where you feel the quality/speed bottleneck most.*
