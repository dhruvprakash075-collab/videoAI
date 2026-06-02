# Implementation Plan — Pipeline Improvements

## Overview

Tasks are sequenced by risk (Phase A → B → D → C). Every change is additive and
config-gated. After each task: `py_compile` touched files and run `pytest tests/ -q`.
Detailed per-item design is in `design.md` / `plan.md`.

**Status as of 2026-05-31 (verified against live code):** Task 1 (config keys) ✅,
Task 2 (VRAM verify) ✅ — add `gc.collect()` before poll, Task 6 (story cache) ✅,
Task 7 (`--yes`) ✅, Task 8 (retry budget) ✅, Task 20 (batch mode) ✅.
Task 14 (token budgeting) is mostly built — downgraded to config-wiring.
Task 24 (delete `config_schemas.py`) is **CANCELLED** — that module is actively
imported and must not be deleted. Baseline: 115 tests pass, 0 errors
(`tmp_root` fixture + `UIState.degradations` reset added to conftest).

## Task Dependency Graph

Tasks are grouped into execution waves. Tasks in the same wave have no
dependencies on each other and may run in parallel; each wave depends on the
prior wave's gate.

```json
{
  "waves": [
    { "wave": 1, "tasks": [1], "description": "Add config keys (unblocks all gated features)" },
    { "wave": 2, "tasks": [2, 3, 4, 5, 6, 7, 8], "description": "Phase A features (independent)" },
    { "wave": 3, "tasks": [9], "description": "Phase A gate" },
    { "wave": 4, "tasks": [10], "description": "Ollama client (enables clean refactors)" },
    { "wave": 5, "tasks": [11, 12, 13, 14, 16, 17, 18, 19, 20], "description": "Phase B + Phase D features" },
    { "wave": 6, "tasks": [15, 21], "description": "Phase B and Phase D gates" },
    { "wave": 7, "tasks": [22], "description": "Staged loop reorder (needs tasks 2 + 10)" },
    { "wave": 8, "tasks": [23, 24], "description": "Phase C gate + flip flag; delete stale schema" }
  ]
}
```

Notes on the graph:
- Task 1 (add config keys) comes first so every gated feature can read its flag.
- Phase A tasks 2–8 are otherwise independent and may run in parallel.
- Phase C (task 22) requires task 2 (verified evict) and task 10 (ollama_client) landed.

## Tasks

## Phase A — Low-risk, independent

- [x] 1. Add new config keys to `config/config.yaml`
  - Add all keys from the corrected "FULL config.yaml additions" block in `plan.md` under their sections (`performance`, `ollama`, `image_gen`, `audio_fx`, `video`, `cache`, `memory`, `music`).
  - Confirm no schema-class edits are needed (loose `Dict` fields + `extra='allow'` in `config/config_schema.py`).
  - DONE: all keys present in config.yaml.
  - _Requirements: 19_

- [x] 2. VRAM-free verification before Stable Diffusion
  - DONE: `gc.collect()` added before poll loop in `_evict_ollama_models()`. `tests/test_vram_evict.py` added.
  - _Requirements: 1_

- [x] 3. One-time character seed resolution + seed lock
  - DONE: `_seed_map` built once before frame loop in `image_gen._stable_diffusion()`. `tests/test_seed_resolution.py` added.
  - _Requirements: 2_

- [x] 4. Program-wide 2-pass loudness normalization
  - DONE: `concatenate_segments()` now accepts `config` param; when `audio_fx.program_loudnorm=True` runs 2-pass EBU R128 with `linear=true`. `tests/test_assembler_loudnorm.py` added.
  - _Requirements: 3_

- [x] 5. Preview SD steps in dry/preview runs
  - DONE: `image_gen._stable_diffusion()` reads `_preview_mode`/`_dry_run` flags from cfg and uses `preview_steps`. Pipeline injects flags into `seg_config["image_gen"]`.
  - _Requirements: 4_

- [x] 6. Cache the invented story
  - DONE in `invent_story()` (~line 2367). `tests/test_story_cache.py` added.
  - _Requirements: 5_

- [x] 7. `--yes` auto-accept flag
  - DONE via `UIState.auto_accept`. `tests/test_autoaccept.py` added.
  - _Requirements: 6_

- [x] 8. Per-segment retry budget
  - DONE in `_process_segment_with_budget` (~line 2294). Test added.
  - _Requirements: 7_

- [ ] 9. Phase A gate
  - `py_compile` all touched files; `pytest tests/ -q` green (existing + new); PTY dry-run completes; verify `/api/status` shape unchanged.
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 19, 20_

## Phase B — Medium

- [ ] 10. Centralized Ollama client with circuit breaker
  - Create `utils/ollama_client.py` (`OllamaClient` with `generate`/`chat`/`stream`, one retry policy, one timeout from `ollama.request_timeout`, per-model breaker).
  - **Breaker = 3 states (web-validated):** Closed → Open after `ollama.breaker_fails` consecutive failures → fail fast for `ollama.breaker_cooldown_s` → **Half-Open** allows ONE probe (success → Closed, failure → Open). Small custom class, no `pybreaker` dep (offline-first).
  - Refactor `director_agent` `_call_ollama*`, `audio_proxy.translate_hinglish`, and eviction/preflight pokes to delegate with identical signatures (no caller changes).
  - Add `tests/test_ollama_client.py` (retry counts, timeout passthrough, breaker opens after N, Half-Open probe closes on success / reopens on failure — mock urlopen).
  - _Requirements: 8_

- [ ] 11. Per-segment degradation ledger + resume badge
  - Add additive `UIState.degradations: list` + `add_degradation(seg, stage, reason)` in `agents/director_agent.py`; call it at every silent-fallback site (SFX skip, mastering→raw, image→black frame, Hyperframes→assembler, translation→English, OmniVoice→silence).
  - Write the list into `run_manifest.json` (`_write_manifest` in `core/pipeline_long.py`). Show a degradation count badge + "RESUMABLE" indicator in `studio_tui.py`. Keep `/api/status` unchanged.
  - Update `tests/conftest.py` to reset `UIState.degradations`; add `tests/test_degradation_ledger.py`.
  - _Requirements: 9, 20_

- [ ] 12. Subtitle model tiny→base for finals
  - In `video/renderer/assembler.py`, use `performance.whisper_model_final` (base) for final runs, `performance.whisper_model` (tiny) for preview/dry; pin to CPU int8; log `whisper=base (cpu)`.
  - Add a test asserting the resolver picks base only for finals.
  - _Requirements: 12_

- [ ] 13. LLM-based world-state extraction (Devanagari-aware)
  - Add `extract_world_state(text, config)` in `utils/specialized_models.py` (resident 3B reviewer → JSON `{characters, facts, open_threads, resolved_threads}`); use it in `memory/memory.py` `WorldState.update` when `memory.llm_world_state` is true; fall back to existing regex on failure/bad JSON.
  - Add `tests/test_world_state.py` (mock 3B JSON parsed; regex fallback on bad JSON).
  - _Requirements: 10_

- [ ] 14. CLIP 77-token budgeting (mostly built — wire config)
  - ALREADY BUILT: `utils/scene_director.py` has `assemble_prompt(identity, scene, style, budget=70)`, `assemble_prompt_multi(identity_list, …, budget=70)`, and `_cap_tokens(text, max_tokens=65)`. Identity is placed first, style anchored, trimmed to ~70 CLIP tokens. `enrich_prompts()` already routes every frame through them.
  - REMAINING (small): read `image_gen.token_budget.{identity,style,scene}` and pass them into `assemble_prompt*` instead of the hardcoded `budget=70` / `0.45` identity split. Reconcile the config numbers (sum 77) with the word×1.3 estimate, leaving ~7 token headroom.
  - OPTIONAL (NOT mandatory): Compel / `lpw_stable_diffusion` for true >77-token weighted embeddings, behind a `try`-import (Compel is not installed; offline-first needs a pinned wheel).
  - Add `tests/test_token_budget.py` (over-budget drops identity tail keeps scene; under-budget unchanged).
  - _Requirements: 11_

- [ ] 15. Phase B gate
  - `py_compile` all; `pytest tests/ -q` green; PTY dry-run; manifest contains `degradations`; `/api/status` unchanged; one Hindi dry-run shows clean world-state.
  - _Requirements: 8, 9, 10, 11, 12, 20_

## Phase D — Robustness & polish

- [ ] 16. OOM auto-recovery ladder
  - In `video/image_gen/image_gen.py` `_stable_diffusion()`, when `image_gen.oom_recovery` is true, step through full → reduced steps → lower res (≥ `image_gen.oom_min_resolution`) → `model_cpu_offload` → CPU fallback → black frame + degradation; append `{tier, from_res, to_res, steps}` to segment meta `oom_events` (extend existing collector). Gate off → current behavior.
  - Add `tests/test_oom_ladder.py` (mock OOM at tier 1–2; assert step-down + meta recorded).
  - _Requirements: 14_

- [ ] 17. Smooth audio joins between segments (cheap alternative)
  - In `video/renderer/assembler.py`, keep the fast `-c:v copy` concat; tune per-segment `afade` (fade-out on each, fade-in on next) to mask joins. Do NOT switch to filter_complex re-encode. Document that true `acrossfade` is deferred.
  - Add `tests/test_audio_crossfade.py` (assert fade tuning present; assert no video re-encode path is forced).
  - _Requirements: 15_

- [ ] 18. Thumbnail generation
  - After the final video exists, when `video.generate_thumbnail` is true, save 1280×720 `studio_outputs/{topic}/thumbnail.png` (hero frame or ~10% frame) and record the path in the manifest (`core/pipeline_long.py` / `video/renderer/`).
  - Add `tests/test_thumbnail.py` (fake images dir → hero-frame selection picks expected file; logic-only).
  - _Requirements: 16_

- [ ] 19. Music auto-ducking
  - In `audio/audio_fx.py` music-mix path, when `music.ducking` is true apply FFmpeg `sidechaincompress` keyed on narration using `music.duck_ratio` (new keys under top-level `music:`). Gate off → static mix.
  - Add `tests/test_music_ducking.py` (assert sidechaincompress with configured ratio when enabled).
  - _Requirements: 18_

- [x] 20. Batch mode (`--topics-file`)
  - Add `--topics-file PATH` to `bootstrap_pipeline.py`; read one topic per line (skip blanks/comments); run `run_long_pipeline` per topic sequentially; continue on failure; write `studio_outputs/batch_report.json` (status, output, degradation count, wall time). Single-topic path unchanged.
  - DONE in `bootstrap_pipeline.py`. REMAINING: add `tests/test_batch_mode.py` (parse file, iteration order, report structure; mock `run_long_pipeline`).
  - _Requirements: 17_

- [ ] 21. Phase D gate
  - `py_compile` touched files; `pytest tests/ -q` green; a dry/real render confirms thumbnail + smooth joins + ducking when enabled; batch report written for a 2-topic batch.
  - _Requirements: 14, 15, 16, 17, 18, 20_

## Phase C — High risk, core-loop surgery (LAST; requires tasks 2 + 10 landed)

- [ ] 22. Staged per-segment loop reorder (behind `performance.staged_loop`)
  - In `core/pipeline_long.py` segment loop / `process_segment()`, add the staged order: text phase (script → review → translate → image prompts, model resident) → single verified evict (task 2) → GPU phase (SD + render). Behind `performance.staged_loop` (default false = today's order).
  - Support `performance.lookahead_segments` > 1: batch the text phase for K segments, persist scripts/prompts to checkpoints before the first evict. Keep `max_workers=1`, never two resident models, checkpoint after each sub-step (additive keys), preserve TTS→whisper adjacency.
  - Add `tests/test_staged_loop.py` (staged_loop:false matches legacy call order; lookahead=2 runs 2 segments' text phase before first evict; checkpoint after each sub-step).
  - _Requirements: 13_

- [ ] 23. Phase C gate + flip flag
  - Prove legacy path (`staged_loop:false`) identical; prove staged path on a real 2-segment run (reduced loads, clean resume); document rollback (set false). Then flip `staged_loop:true`.
  - _Requirements: 13, 20_

## Cleanup

- [x] 24. Delete stale duplicate schema — CANCELLED
  - INVESTIGATED: `config/config_schemas.py` (plural) is NOT stale. It is actively imported by `config/__init__.py`, `agents/decision_engine.py`, `memory/blackboard.py`, `tests/test_blackboard.py`, `tests/test_decision_record.py`, `tests/test_decision_engine.py`. It holds the decision/vision data models (`DecisionRecord`, `VisionDocument`, `WriterBreakdown`, `ConfigOverlay`, …) — a different module from `config_schema.py` (singular). **DO NOT DELETE.** Task cancelled.
  - _Requirements: 19_

## Notes

- All work is additive and config-gated; default config reproduces today's behavior.
- Verify each task: `py_compile` touched files + `pytest tests/ -q` + (where noted) a PTY dry-run.
- Phase gates (tasks 9, 15, 21, 23) must be green before starting the next phase.
- No schema-class edits are needed — new config keys pass through `extra='allow'` + loose `Dict` fields.
- The deep design (per-item file/function/test detail + code-review corrections) is in `plan.md`.
