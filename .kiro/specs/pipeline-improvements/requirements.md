# Requirements Document

## Introduction

Video.AI is a local, offline-first narrative video-generation engine tuned for a
6GB RTX 4050 GPU. This spec hardens and extends the existing pipeline with a set
of reviewed, risk-sequenced improvements (VRAM safety, audio quality, resilience,
resource discipline, and operator convenience). Every change is **additive and
config-gated** so the current behavior is preserved unless a flag is turned on.

The full technical design and the verified code-review corrections live in
`plan.md` (the deep design document for this spec).

### Global invariants (apply to every requirement)

- Only ONE Ollama model resident at a time; `_evict_ollama_models()` runs before any GPU/SD task.
- `performance.max_workers` stays `1` (no parallel segments).
- UIState changes are additive (new fields with safe defaults); FastAPI `/api/status` keeps returning `{status, active_question, logs, output_video}`.
- No new mandatory network calls (offline-first).
- CLI, web dashboard, and all existing pytest tests must keep passing.
- New config keys pass through automatically (`extra='allow'` + loose `Dict` fields in `config/config_schema.py`) — **no schema-class edits required.**

## Glossary

- **Segment:** one fixed-length unit of the video produced by `process_segment()`.
- **Evict:** unloading the resident Ollama model (`keep_alive=0`) to free VRAM before Stable Diffusion.
- **Degradation:** a silent quality fallback (e.g. image → black frame, translation → English) recorded for the operator.
- **Loudnorm:** FFmpeg EBU R128 loudness normalization (`loudnorm` filter).
- **Staged loop:** reordered per-segment flow that runs all LLM/text work before the GPU phase.
- **Circuit breaker:** logic that fails fast after repeated Ollama failures instead of retrying every call.
- **UIState:** shared in-memory state class (in `agents/director_agent.py`) read by the TUI and FastAPI status API.

## Requirements

### Requirement 1: Verify VRAM is actually free before Stable Diffusion (A1)

**User Story:** As an operator on a 6GB GPU, I want the pipeline to confirm VRAM is
freed before loading Stable Diffusion, so image generation does not crash with OOM
right after an LLM call.

#### Acceptance Criteria
1. WHEN `_evict_ollama_models()` runs THEN the system SHALL poll free VRAM (sleep 0.5s, up to `performance.vram_evict_wait_s`) until free VRAM ≥ `performance.vram_sd_threshold_gb`.
2. IF VRAM never frees within the wait window THEN the system SHALL log a loud WARNING, attempt one harder evict (re-poke the resident model via `/api/ps`), and proceed anyway.
3. IF CUDA is not available THEN the system SHALL skip the poll and return immediately.
4. WHEN the poll succeeds THEN the system SHALL log the free VRAM amount before SD loads.

### Requirement 2: One-time character seed resolution + seed lock (A2)

**User Story:** As an operator, I want consistent character appearance and no
per-frame disk scanning, so generation is faster and visually stable across resumes.

#### Acceptance Criteria
1. WHEN `_stable_diffusion()` starts THEN the system SHALL build a `seed_map` (character → seed) ONCE before the frame loop by reading project JSONs a single time.
2. WHEN generating each frame THEN the system SHALL look the seed up from `seed_map` instead of re-scanning the project directory.
3. IF `image_gen.lock_seed` is true THEN the system SHALL derive a stable base seed from the topic hash so the same character stays visually consistent across segments and resumes.
4. IF no character locks exist THEN behavior SHALL be identical to today.

### Requirement 3: Program-wide 2-pass loudness normalization (A3)

**User Story:** As a viewer, I want consistent loudness across the whole video, so
segment seams do not pump or jump in volume.

#### Acceptance Criteria
1. IF `audio_fx.program_loudnorm` is true THEN the system SHALL run one program-wide FFmpeg `loudnorm` (2-pass: measure then apply) on the full concatenated audio targeting `audio_fx.target_lufs` (default -14).
2. WHEN applying the second pass THEN the command SHALL include `linear=true` to force predictable linear scaling.
3. The loudnorm SHALL be implemented per concat branch (no-music branch via `-af`; music branch appended to the `filter_complex` `[outa]` chain).
4. IF `audio_fx.program_loudnorm` is false THEN behavior SHALL be identical to today.

### Requirement 4: Preview SD steps in dry/preview runs (A4)

**User Story:** As an operator doing quick checks, I want fewer diffusion steps in
preview/dry runs, so iteration is faster.

#### Acceptance Criteria
1. WHEN `dry_run` or `preview_mode` is active THEN the system SHALL use `image_gen.preview_steps` (default 8) instead of full `steps`.
2. WHEN preview steps are used THEN the system SHALL log `steps=N (preview)`.
3. WHEN neither flag is set THEN full configured steps SHALL be used.

### Requirement 5: Cache the invented story (A5)

**User Story:** As an operator re-running the same topic, I want the invented story
reused, so I don't pay the invent-LLM cost twice.

#### Acceptance Criteria
1. WHEN a story is invented THEN the system SHALL write it to `cache/story_{topic_hash}.json` IF `cache.cache_invented_story` is true.
2. WHEN re-running the same topic THEN the system SHALL load the cached story and log "story cache hit", UNLESS `--no-resume` or a `force_refresh` flag is set.
3. The cache behavior SHALL mirror the existing vision-doc cache pattern.

### Requirement 6: `--yes` auto-accept flag (A6)

**User Story:** As an operator running unattended, I want a `--yes` flag that
auto-accepts prompts, so runs complete without manual input.

#### Acceptance Criteria
1. WHEN `--yes` is passed THEN `consult_user`/`consult_fields` SHALL return the default option/values without prompting, even on a TTY.
2. WHEN `--yes` is absent THEN interactive behavior SHALL be unchanged.
3. WHEN `--yes --dry-run` is used THEN a full run SHALL complete with zero prompts.

### Requirement 7: Per-segment retry budget (A7)

**User Story:** As an operator on long runs, I want a cap on per-segment retries, so
one bad segment cannot stall the whole video.

#### Acceptance Criteria
1. WHEN a segment fails THEN the system SHALL retry at most `performance.max_segment_retries` (default 2) times.
2. WHEN the retry budget is exhausted THEN the system SHALL log it, record a degradation (Requirement 9), skip the segment, and continue the run.

### Requirement 8: Centralized Ollama client with circuit breaker (B1)

**User Story:** As a maintainer, I want one Ollama client with unified retry/timeout
and a circuit breaker, so failures are handled consistently and don't hammer a dead server.

#### Acceptance Criteria
1. The system SHALL provide `utils/ollama_client.py` with `OllamaClient` exposing `generate`, `chat`, and `stream`, one retry policy, and one timeout source (`ollama.request_timeout`).
2. WHEN a model fails `ollama.breaker_fails` (default 3) times consecutively THEN the breaker SHALL fail fast for `ollama.breaker_cooldown_s` (default 30) instead of full backoff.
3. Existing call sites (`director_agent` `_call_ollama*`, `audio_proxy.translate_hinglish`, eviction/preflight pokes) SHALL delegate to the client with identical signatures (no caller changes).
4. Happy-path behavior SHALL be identical to today.

### Requirement 9: Per-segment degradation ledger + resume badge (B2)

**User Story:** As an operator, I want a record of every silent fallback and a resume
indicator, so I know exactly where quality dropped.

#### Acceptance Criteria
1. UIState SHALL gain `degradations: list = []` (additive) with `add_degradation(seg, stage, reason)`.
2. The system SHALL call `add_degradation` at each silent-fallback site: SFX skip, mastering→raw copy, image→black frame, Hyperframes→assembler, translation→English, OmniVoice→silence.
3. The degradation list SHALL be written into `run_manifest.json`.
4. The TUI status panel SHALL show a degradation count badge and a "RESUMABLE" indicator when a checkpoint exists for the topic.
5. `/api/status` shape SHALL remain unchanged.

### Requirement 10: LLM-based world-state extraction (B3)

**User Story:** As an operator using non-English topics, I want world-state extracted
by the LLM (Devanagari-aware), so continuity tracking isn't polluted by regex noise.

#### Acceptance Criteria
1. IF `memory.llm_world_state` is true THEN `WorldState.update` SHALL use a small `extract_world_state(text, config)` (resident 3B reviewer) returning `{characters, facts, open_threads, resolved_threads}`.
2. IF the LLM call fails/times out OR returns bad JSON THEN the system SHALL fall back to the existing regex extractor (kept intact).
3. The extractor SHALL reuse the existing 3B model slot (no extra resident model).

### Requirement 11: CLIP 77-token budgeting (B4)

**User Story:** As an operator, I want long prompts budgeted so the scene survives, so
images match the action even when the character/style text is long.

#### Acceptance Criteria
1. WHEN an assembled prompt exceeds 77 tokens THEN the system SHALL budget tokens (reserve ~`token_budget.identity` for identity, ~`token_budget.style` for style, rest for scene) and drop the lowest-weight character / abbreviate style so the scene text survives.
2. IF the prompt is under 77 tokens THEN output SHALL be identical to today.
3. The system MAY use Compel-weighted long prompts if Compel is installed.

### Requirement 12: Subtitle model tiny→base for finals (B5)

**User Story:** As a viewer, I want accurate subtitles on final renders, so timing and
words are correct, without that model eating VRAM during SD.

#### Acceptance Criteria
1. WHEN rendering a final (non-preview/non-dry) run THEN the system SHALL use `performance.whisper_model_final` (default "base"); otherwise `performance.whisper_model` (tiny).
2. The whisper model SHALL be pinned to CPU int8 so it does not sit in VRAM during SD.
3. The system SHALL log the resolved model and device (e.g. `whisper=base (cpu)`).

### Requirement 13: Staged per-segment loop reorder (C1, flagged)

**User Story:** As an operator, I want all LLM work batched before the GPU phase, so the
6GB GPU loads each model fewer times per segment.

#### Acceptance Criteria
1. IF `performance.staged_loop` is false (default) THEN the per-segment order SHALL be identical to today.
2. IF `performance.staged_loop` is true THEN the system SHALL run all text-phase LLM work (script → review → translate → image prompts) keeping the model resident, then a single verified evict (Requirement 1), then the GPU phase (SD + render).
3. IF `performance.lookahead_segments` > 1 THEN the text phase for the next K segments SHALL be batched and persisted to checkpoints before the first evict.
4. `max_workers` SHALL stay 1 and two models SHALL never be resident at once.
5. The system SHALL checkpoint after each sub-step so resume works mid-phase, with additive checkpoint keys.
6. TTS→whisper adjacency SHALL be preserved so subtitle timing cannot desync from audio.

### Requirement 14: OOM auto-recovery ladder (D1)

**User Story:** As an operator, I want a clear OOM step-down ladder, so a frame is always
produced (or a degradation recorded) instead of a crash.

#### Acceptance Criteria
1. IF `image_gen.oom_recovery` is true AND a CUDA OOM occurs THEN the system SHALL step through: full → reduced steps → lower resolution (≥ `image_gen.oom_min_resolution`) → `model_cpu_offload` → CPU fallback → black frame + degradation.
2. WHEN each tier is attempted THEN the system SHALL append `{tier, from_res, to_res, steps}` to the segment meta JSON `oom_events`.
3. IF `image_gen.oom_recovery` is false THEN behavior SHALL be unchanged.

### Requirement 15: Smooth audio joins between segments (D2, cheap alternative)

**User Story:** As a viewer, I want segment audio joins to sound smooth, without a slow
re-encode of the whole video.

#### Acceptance Criteria
1. The system SHALL keep the fast copy-concat path (`-c:v copy`) and SHALL NOT switch to a full filter_complex re-encode just for crossfade.
2. The system SHALL ensure each segment has a short audio fade-out and the next a fade-in (tuning the existing per-segment `afade`) to mask the join.
3. True `acrossfade` between segments SHALL be deferred (documented), because it requires per-segment inputs + video re-encode.

### Requirement 16: Thumbnail generation (D3)

**User Story:** As an operator, I want a thumbnail produced for each finished video, so
I can preview/publish it easily.

#### Acceptance Criteria
1. IF `video.generate_thumbnail` is true THEN after the final video exists the system SHALL save a 1280×720 `studio_outputs/{topic}/thumbnail.png` (hero frame or a frame at ~10%).
2. The thumbnail path SHALL be recorded in the manifest.
3. This SHALL be additive (new file + manifest key only).

### Requirement 17: Batch mode (`--topics-file`) (D4)

**User Story:** As an operator, I want to queue many topics from a file, so I can run
batches unattended overnight.

#### Acceptance Criteria
1. WHEN `--topics-file PATH` is passed THEN the system SHALL read one topic per line (ignoring blanks/comments) and run `run_long_pipeline` for each sequentially.
2. IF a topic fails THEN the system SHALL log the result and continue to the next topic.
3. The system SHALL write `studio_outputs/batch_report.json` summarizing each run (status, output, degradation count, wall time).
4. The single-topic path SHALL be unchanged.

### Requirement 18: Music auto-ducking (D5)

**User Story:** As a viewer, I want background music to drop under narration, so speech
is always clear.

#### Acceptance Criteria
1. IF `music.ducking` is true THEN the music-mix path SHALL apply an FFmpeg `sidechaincompress` keyed on the narration, using `music.duck_ratio` (default 0.3).
2. The ducking keys SHALL live under the top-level `music:` section (NOT `audio_fx`; `audio_fx.duck_ratio` does not exist).
3. IF `music.ducking` is false THEN the current static mix SHALL be used.

### Requirement 19: Configuration & cleanup (cross-cutting)

**User Story:** As a maintainer, I want all new tunables in one place and stale files
removed, so config stays clean and discoverable.

#### Acceptance Criteria
1. All new keys SHALL be added to `config/config.yaml` under their sections (see the "FULL config.yaml additions" block in `plan.md`).
2. Values SHALL be read via `config.get(section, {}).get(key, default)` — never hardcoded.
3. No edits to schema classes SHALL be required (loose Dict fields + `extra='allow'`).
4. After grep-confirming no imports, stale `config/config_schemas.py` (plural) MAY be deleted (`config/config_schema.py` singular is the active one).

### Requirement 20: Tests (cross-cutting)

**User Story:** As a maintainer, I want each change covered by tests, so regressions are caught.

#### Acceptance Criteria
1. The following pytest modules SHALL be added under `tests/`: test_vram_evict, test_seed_resolution, test_assembler_loudnorm, test_story_cache, test_autoaccept, test_ollama_client, test_degradation_ledger, test_world_state, test_token_budget, test_staged_loop, test_oom_ladder, test_audio_crossfade, test_thumbnail, test_batch_mode, test_music_ducking.
2. `tests/conftest.py` SHALL also reset `UIState.degradations`.
3. The full suite (existing 31 + new) SHALL pass at each phase gate.
