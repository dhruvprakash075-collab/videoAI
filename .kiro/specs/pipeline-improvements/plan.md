# Pipeline Improvements — Implementation Plan (consolidated)

> **Status legend:** ✅ done · 🟡 partial · ❌ not started
> Last verified against live code + installed env on **2026-05-31**.
> Env: diffusers 0.37.1, torch 2.11.0+cu128 (CUDA on), Compel **not** installed,
> textual / fastapi / faster-whisper / pydub present. Tests: **115 pass, 0 errors**.

This is the authoritative detail doc for the `pipeline-improvements` spec
(`requirements.md` = the 20 requirements, `tasks.md` = the 24 build tasks). It folds
in all prior review passes and web/local validation so there is ONE source of truth.

---

## Invariants — DO NOT BREAK (check after every task)

- **6GB rule:** only ONE Ollama model resident at a time; `_evict_ollama_models()` runs before any GPU/SD task.
- **Serial:** `performance.max_workers = 1` stays 1. No parallel segments.
- **Additive UIState:** new fields only, with safe defaults. FastAPI `/api/status` must keep returning `{status, active_question, logs, output_video}`.
- **Offline-first:** no new *mandatory* network calls or deps. edge-tts/web-search/Compel stay optional.
- **Backward compat:** CLI (`bootstrap_pipeline.py`), web dashboard, and the full pytest suite must stay green.
- **No schema-class edits:** `performance`/`audio_fx`/`video`/`cache`/`memory`/`music`/`image_gen` are loose `Dict` (or `extra='allow'`) in `config/config_schema.py`, so new YAML keys pass through automatically. Read them via `config.get(section, {}).get(key, default)`.
- **Verify each task:** `py_compile` touched files + `venv\Scripts\python.exe -m pytest tests/ -q` + (where noted) a PTY dry-run.

---

## ⚠️ Two corrections that override earlier drafts

1. **DO NOT delete `config/config_schemas.py` (plural).** It is NOT stale. It is actively
   imported by `config/__init__.py`, `agents/decision_engine.py`, `memory/blackboard.py`,
   and three test files. It holds the **decision/vision data models** (`DecisionRecord`,
   `VisionDocument`, `WriterBreakdown`, `ConfigOverlay`, `build_default_decision_record`, …) —
   a different module from `config_schema.py` (singular, YAML validation). Task 24 is **cancelled**.
2. **Config keys are already in `config.yaml`.** Task 1 is done — every key below is present.

---

## Implementation status (verified)

| ID | Item | File(s) | Status | Note |
|----|------|---------|--------|------|
| — | Config keys added | `config/config.yaml` | ✅ | all keys present |
| A1 | VRAM-free verify before SD | `core/pipeline_long.py` | ✅ | poll + `/api/ps` harder evict done; **add `gc.collect()`** |
| A2 | Seed-map built once + seed lock | `video/image_gen/image_gen.py` | ❌ | still scans `PROJECTS_ROOT.iterdir()` per frame (~line 406) |
| A3 | Program-wide 2-pass loudnorm | `video/renderer/assembler.py` | ❌ | |
| A4 | Preview SD steps | `video/image_gen/image_gen.py` | ❌ | config has `preview_steps:8`, not read |
| A5 | Cache invented story | `agents/director_agent.py` | ✅ | `invent_story()` ~line 2367 |
| A6 | `--yes` auto-accept | `bootstrap_pipeline.py`, `director_agent.py` | ✅ | `UIState.auto_accept` |
| A7 | Per-segment retry budget | `core/pipeline_long.py` | ✅ | `_process_segment_with_budget` ~line 2294 |
| B1 | Ollama client + circuit breaker | `utils/ollama_client.py` (new) | ❌ | **add Half-Open state** |
| B2 | Degradation ledger + resume badge | `director_agent.py`, `pipeline_long.py`, `studio_tui.py` | 🟡 | `UIState.degradations`+`add_degradation` exist; 2 of 6 sites wired; not in manifest; no TUI badge |
| B3 | LLM world-state (Devanagari) | `memory/memory.py`, `utils/specialized_models.py` | ❌ | regex only today |
| B4 | CLIP 77-token budgeting | `utils/scene_director.py`, `image_gen.py` | 🟡 | **assembler already built**; only needs config-key wiring (see Task 14) |
| B5 | Whisper tiny→base for finals | `video/renderer/assembler.py` | ❌ | reads only `whisper_model` |
| C1 | Staged loop reorder (flagged) | `core/pipeline_long.py` | ❌ | last, behind flag |
| D1 | OOM auto-recovery ladder | `video/image_gen/image_gen.py` | 🟡 | 3-tier exists; extend to full ladder + meta log |
| D2 | Smooth audio joins (cheap) | `video/renderer/assembler.py` | ❌ | tune per-segment fades, keep copy-concat |
| D3 | Thumbnail generation | `pipeline_long.py` / renderer | ❌ | |
| D4 | Batch mode `--topics-file` | `bootstrap_pipeline.py` | ✅ | + `batch_report.json` |
| D5 | Music auto-ducking | `audio/audio_fx.py` | ❌ | `sidechaincompress` |
| — | Preflight TUI fix | `studio_tui.py` | ✅ | background thread |
| — | `tmp_root` test fixture + `degradations` reset | `tests/conftest.py` | ✅ | fixed 2026-05-31, 115 pass |

**Remaining work:** A2, A3, A4, A8(=B1), B2 (finish), B3, B5, C1, D1 (extend), D2, D3, D5, and the B4 config-wiring. Plus the 15 pytest modules.

---

## PHASE A — Low-risk quick wins

### A1 · Verify VRAM actually freed before SD ✅ (one tweak)
- **Done:** `_evict_ollama_models()` polls `torch.cuda.mem_get_info()` (0.5s, up to `performance.vram_evict_wait_s`) until free ≥ `performance.vram_sd_threshold_gb`, then a harder evict via `/api/ps`, then proceeds.
- **Tweak (web-validated):** call `gc.collect()` once **before** the poll loop — `empty_cache()` alone is unreliable; `del → gc.collect() → empty_cache() → poll` is the robust order.
- **Test:** `tests/test_vram_evict.py` — mock `mem_get_info` low-then-high; assert wait-then-proceed; no-CUDA returns immediately.

### A2 · Seed-map built once + seed lock ❌
- **File:** `video/image_gen/image_gen.py` `_stable_diffusion()`.
- **Change:** before the frame loop, build `seed_map: dict[char -> seed]` by reading project JSONs **once**. Inside the loop, look up from the dict instead of `PROJECTS_ROOT.iterdir()` every frame.
- Keep `torch.Generator(device="cuda").manual_seed(seed)` per frame (correct). If `image_gen.lock_seed` is true, derive the base seed from the topic hash. No-lock behavior identical to today.
- **Test:** `tests/test_seed_resolution.py` — fake project dirs → seed_map built once, lookups match.

### A3 · Program-wide 2-pass loudnorm ❌ (web-validated recipe)
- **File:** `video/renderer/assembler.py` `concatenate_segments()`, gated on `audio_fx.program_loudnorm`.
- **Pass 1 (measure):** `loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json` → parse `measured_I/TP/LRA/thresh/offset` from **stderr** JSON.
- **Pass 2 (apply):** `loudnorm=I=…:TP=-1.5:LRA=11:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=…:linear=true`. `linear=true` is required (prevents dynamic-mode fallback).
- Per branch: no-music branch via `-af`; music branch appended to the `filter_complex` `[outa]` chain. Gate off → today's behavior.
- **Test:** `tests/test_assembler_loudnorm.py` — 2-pass present when enabled, absent when disabled.

### A4 · Preview SD steps ❌
- **File:** `video/image_gen/image_gen.py`. When `dry_run`/`preview_mode`, use `image_gen.preview_steps` (8) instead of full `steps`; log `steps=N (preview)`.
- **Test:** resolver returns preview steps under the flag, full steps otherwise.

### A5 · Cache invented story ✅
- Done in `director_agent.invent_story()`: reads `cache.cache_invented_story`, writes `cache/story_{hash}.json`, logs `A5: story cache hit`, bypassed by `--no-resume`/`force_refresh`.
- **Test (still to add):** `tests/test_story_cache.py` — round-trip, hash keying, force_refresh bypass.

### A6 · `--yes` auto-accept ✅
- Done: `bootstrap_pipeline.py --yes` → `UIState.auto_accept`; `consult_user`/`consult_fields` return defaults. **Test:** `tests/test_autoaccept.py`.

### A7 · Per-segment retry budget ✅
- Done: `_process_segment_with_budget` caps at `performance.max_segment_retries`, records `add_degradation` on exhaustion, skips and continues. **Test:** assert loop stops at budget.

**PHASE A GATE:** py_compile touched files; pytest green; PTY dry-run; `/api/status` shape unchanged.

---

## PHASE B — Medium

### B1 · `utils/ollama_client.py` + circuit breaker ❌ (web-validated: 3 states)
- **New file:** `OllamaClient` with `generate(prompt, model, format_json=False, seed=None)`, `chat(messages, model)`, `stream(prompt, model)`. One retry policy, one timeout (`ollama.request_timeout`).
- **Breaker = three states (Closed → Open → Half-Open):** open after `ollama.breaker_fails` (3) consecutive failures; fail fast for `ollama.breaker_cooldown_s` (30); then **Half-Open** allows ONE probe — success → Closed, failure → Open. Per-model.
- Small custom class — **no `pybreaker` dep** (offline-first). Follow the `error-handling` skill (typed errors, retry only transient failures, never swallow silently).
- **Refactor (identical signatures, no caller changes):** `director_agent._call_ollama*`, `audio_proxy.translate_hinglish`, eviction/preflight pokes delegate to the client.
- **Test:** `tests/test_ollama_client.py` — retry counts, timeout passthrough, breaker opens after N, Half-Open probe closes on success / reopens on failure (mock urlopen).

### B2 · Degradation ledger → manifest + TUI badge 🟡 (finish it)
- **Done:** `UIState.degradations` + `add_degradation(seg, stage, reason)`; called at `tts_silence_fallback` and `segment_skip`.
- **To do:** call `add_degradation` at the remaining silent-fallback sites — SFX skip (`audio_fx.mix_sfx`), mastering→raw copy (`audio_fx.master_audio`), image→black frame (`image_gen`), Hyperframes→assembler (`renderer.render_with_assets`), translation→English (`audio_proxy.translate_hinglish`), OmniVoice→silence (if not already). Write the list into `run_manifest.json` (`_write_manifest`). Show a count badge + "RESUMABLE" in `studio_tui.py`.
- **Test:** `tests/test_degradation_ledger.py` (conftest already resets `UIState.degradations`).

### B3 · LLM world-state extraction (Devanagari-aware) ❌
- **Files:** `utils/specialized_models.py` (`extract_world_state(text, config)` via the resident 3B reviewer → `{characters, facts, open_threads, resolved_threads}` JSON); `memory/memory.py` `WorldState.update` prefers it when `memory.llm_world_state` is true, falls back to the existing regex on any failure/bad JSON. Reuse the existing 3B slot (no new resident model).
- **Test:** `tests/test_world_state.py` — mock 3B JSON parsed; regex fallback on bad JSON.

### B4 · CLIP 77-token budgeting 🟡 (mostly built — just wire config)
- **Already built:** `utils/scene_director.py` `assemble_prompt(identity, scene, style, budget=70)`, `assemble_prompt_multi(identity_list, …, budget=70)`, `_cap_tokens(text, max_tokens=65)`. Identity goes first, style anchored, trimmed to ~70 CLIP tokens. `enrich_prompts()` already routes every frame through them.
- **Remaining (small):** read `image_gen.token_budget.{identity, style, scene}` and pass them into `assemble_prompt*` instead of the hardcoded `budget=70` / `0.45` split.
- **Optional (do NOT make mandatory):** Compel/`lpw_stable_diffusion` for true >77-token weighted embeddings — Compel is not installed and offline-first requires a pinned wheel. Keep behind a `try`-import per Requirement 11's "MAY use Compel".
- **Test:** `tests/test_token_budget.py` — over-budget drops identity tail, keeps scene; under-budget unchanged.

### B5 · Whisper tiny→base for finals ❌
- **File:** `video/renderer/assembler.py` `_get_whisper_model()`. Use `performance.whisper_model_final` (base) for final (non-preview/non-dry) runs, `performance.whisper_model` (tiny) otherwise. Pin to **CPU int8** so it never sits in VRAM during SD. Log `whisper=base (cpu)`.
- **Test:** resolver picks base only for finals.

**PHASE B GATE:** py_compile all; pytest green; PTY dry-run; manifest contains `degradations`; `/api/status` unchanged; one Hindi dry-run shows clean world-state.

---

## PHASE C — High risk, core-loop surgery (LAST; needs A1 + B1 landed)

### C1 · Staged per-segment loop reorder (flagged) ❌
- **File:** `core/pipeline_long.py` segment loop / `process_segment()`.
- **Staged order (behind `performance.staged_loop`, default false = today's order):** text phase (script → review → translate → image prompts, model resident) → **single verified evict** (A1) → GPU phase (SD + render).
- `performance.lookahead_segments` > 1 → batch the text phase for K segments, persist scripts/prompts to checkpoints before the first evict.
- **Hard constraints:** `max_workers=1`; never two resident models; checkpoint after each sub-step (additive keys); **preserve TTS→whisper adjacency** so subtitle timing can't desync.
- **Rollback:** set `staged_loop:false`.
- **Test:** `tests/test_staged_loop.py` — staged_loop:false matches legacy order; lookahead=2 runs 2 segments' text phase before first evict; checkpoint after each sub-step.

**PHASE C GATE:** legacy path proven identical; staged path proven on a real 2-segment CUDA run (fewer model loads, clean resume); then flip `staged_loop:true`.

---

## PHASE D — Robustness & polish (independent; slot after Phase A)

### D1 · OOM auto-recovery ladder 🟡 (extend existing)
- **File:** `video/image_gen/image_gen.py` `_stable_diffusion()`. Today's 3-tier (full → reduced steps → CPU) becomes the full ordered ladder when `image_gen.oom_recovery` is true: full → reduced steps → lower res (≥ `image_gen.oom_min_resolution`) → `model_cpu_offload` → CPU → black frame + `add_degradation`.
- Append `{tier, from_res, to_res, steps}` to `segment_NN_meta.json` `oom_events` (extend the existing `_oom_events` collector). Gate off → current behavior.
- **Test:** `tests/test_oom_ladder.py` — mock OOM at tier 1–2 → step-down + meta recorded.

### D2 · Smooth audio joins (cheap alternative) ❌
- **File:** `video/renderer/assembler.py`. Keep the fast `-c:v copy` concat. Tune the per-segment `afade` (fade-out on each segment, fade-in on the next) to mask joins. **Do NOT** switch to a filter_complex re-encode for true `acrossfade` (defer it; documented — it needs per-segment inputs + video re-encode).
- **Test:** `tests/test_audio_crossfade.py` — fade tuning present; no forced video re-encode.

### D3 · Thumbnail generation ❌
- After the final video exists and `video.generate_thumbnail` is true, save 1280×720 `studio_outputs/{topic}/thumbnail.png` (hero frame or ffmpeg frame at ~10%); record the path in the manifest.
- **Test:** `tests/test_thumbnail.py` — hero-frame selection picks expected file (logic-only).

### D4 · Batch mode `--topics-file` ✅
- Done in `bootstrap_pipeline.py`: one topic per line (skips blanks/`#`), runs each sequentially, continues on failure, writes `studio_outputs/batch_report.json` (status, output, degradation count, wall time). Pairs with `--yes`.
- **Test (to add):** `tests/test_batch_mode.py` — parse file, iteration order, report structure (mock `run_long_pipeline`).

### D5 · Music auto-ducking ❌ (web-validated recipe)
- **File:** `audio/audio_fx.py` music-mix path, gated on `music.ducking`. `asplit` the narration into a mix copy + a sidechain key; `[music][voicekey]sidechaincompress=threshold=0.05:ratio={music.duck_ratio mapped}:attack=20:release=300[ducked]`; then `amix`/`amerge` `[voice][ducked]`. Gate off → static mix.
- **Test:** `tests/test_music_ducking.py` — `sidechaincompress` present with configured ratio when enabled.

**PHASE D GATE:** py_compile; pytest green; a dry/real render confirms thumbnail + smooth joins + ducking when enabled; batch report written for a 2-topic batch.

---

## Config keys (already in `config.yaml` — verified present)

```yaml
performance:
  vram_evict_wait_s: 15
  vram_sd_threshold_gb: 4.5
  max_segment_retries: 2
  whisper_model_final: "base"
  staged_loop: false
  lookahead_segments: 1
ollama:
  breaker_fails: 3
  breaker_cooldown_s: 30
image_gen:
  lock_seed: true
  preview_steps: 8
  oom_recovery: true
  oom_min_resolution: "640x360"
  token_budget: { identity: 25, style: 20, scene: 32 }
audio_fx:
  program_loudnorm: true
  loudnorm_two_pass: true
  target_lufs: -14
video:
  audio_crossfade_ms: 200
  generate_thumbnail: true
cache:
  cache_invented_story: true
memory:
  llm_world_state: false
music:
  ducking: true
  duck_ratio: 0.3
```

> Note: `image_gen.token_budget` totals 77; the assembler currently uses an internal
> budget of 70 with a 45% identity share. When wiring B4, reconcile the config numbers
> with the assembler's word×1.3 estimate (treat them as CLIP-token targets, leave ~7 headroom).

---

## Test list (pytest, `tests/`)

`test_vram_evict`, `test_seed_resolution`, `test_assembler_loudnorm`, `test_story_cache`,
`test_autoaccept`, `test_ollama_client`, `test_degradation_ledger`, `test_world_state`,
`test_token_budget`, `test_staged_loop`, `test_oom_ladder`, `test_audio_crossfade`,
`test_thumbnail`, `test_batch_mode`, `test_music_ducking`.

- `tests/conftest.py` already resets `UIState.degradations` and provides the `tmp_root` fixture (added 2026-05-31).
- Baseline before this spec's new tests: **115 pass, 0 errors.**

---

## Recommended build order (value ÷ effort, deps respected)

1. **Finish the easy gaps first:** A1 `gc.collect()` tweak → A2 (seed-map) → A4 (preview steps) → B4 (config-wiring) → B5 (whisper) → **Phase A/B-quick gate**.
2. **B1** (ollama_client + Half-Open) — unblocks clean refactors and C1.
3. **B2** (finish all 6 degradation sites + manifest + TUI badge).
4. **A3** (loudnorm) → **D5** (ducking) → **D2** (fade tuning) — all audio, do together.
5. **D1** (OOM ladder) → **D3** (thumbnail).
6. **B3** (LLM world-state) → **Phase B/D gate**.
7. **C1** (staged loop, behind flag) LAST → flip `staged_loop:true` after a real CUDA run.
8. Write each test module alongside its task; run the suite at every gate.

---

## Web/local validation summary (2026-05-31)

- **diffusers 0.37.1 / torch 2.11.0+cu128 / CUDA on / Compel NOT installed** — confirmed in venv.
- **A3 loudnorm:** 2-pass measure→apply with `linear=true` is the correct EBU R128 method; -14 LUFS = streaming.
- **D5 ducking:** `sidechaincompress` is the right FFmpeg filter; needs `asplit` on the narration.
- **B1 breaker:** standard pattern is 3-state incl. Half-Open probe — added to the plan.
- **A1 VRAM:** `empty_cache()` alone unreliable; add `gc.collect()` before polling.
- **B4 tokens:** budgeting already implemented in `scene_director.py`; keep it, only wire config; Compel optional.
- **config_schemas.py:** keep — it's the decision/vision model module, not a duplicate.
