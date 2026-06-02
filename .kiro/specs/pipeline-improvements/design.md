# Design Document

> The authoritative, fully-detailed design (per-item file/function/config/test breakdown,
> dependency graph, code-review corrections, and web-validated FFmpeg findings) lives in
> **`plan.md`** in this same folder. This document is the architecture summary that maps
> the requirements to that plan.

## Overview

All improvements are **additive and config-gated**. The default config reproduces today's
behavior exactly; each feature turns on via a flag in `config/config.yaml`. No schema-class
edits are needed because `config/config_schema.py` uses loose `Dict[str, Any]` sections and
`extra='allow'` on model configs, so new keys pass through automatically.

Work is sequenced by risk into four phases. Low-risk, independent wins land first; the
core per-segment loop rewrite lands last behind a flag.

## Architecture

The pipeline flow is unchanged; these improvements harden existing stages:

```
CLI/UI → bootstrap → pipeline_long → Director (plan) → Writer (script) → Reviewer
       → translate → TTS/RVC/SFX (audio/) → evict LLM → Stable Diffusion (video/image_gen)
       → render segments → concatenate (video/renderer) → final MP4
       ↕ StoryMemory (memory/) for continuity, Checkpoints for resume
```

Phase sequencing (risk-ordered):

```
Phase A (low risk, independent)   → A1..A7   VRAM verify, seed map, loudnorm, preview steps,
                                              story cache, --yes, retry budget
Phase B (medium)                  → B1..B5   ollama_client+breaker, degradation ledger,
                                              world-state LLM, token budget, subtitle base
Phase D (robustness/polish)       → D1..D5   OOM ladder, smooth joins, thumbnail, batch, ducking
Phase C (high risk, core loop)    → C1       staged loop reorder; requires A1 + B1 landed
```

Build order (value ÷ effort, deps respected): **A1–A7 → gate → B1 → B2 → B5 → D1 → D2 →
D3 → gate → B3 → B4 → D5 → gate → D4 → C1 (flagged) → gate → flip `staged_loop:true`.**

## Components and Interfaces

| Req | File | Function / anchor |
|-----|------|-------------------|
| 1 VRAM verify | `core/pipeline_long.py` | `_evict_ollama_models()` |
| 2 seed map / lock | `video/image_gen/image_gen.py` | `_stable_diffusion()` |
| 3 program loudnorm | `video/renderer/assembler.py` | `concatenate_segments()` (both branches) |
| 4 preview steps | `video/image_gen/image_gen.py` + pipeline | step resolver |
| 5 story cache | `agents/director_agent.py` | `invent_story()` |
| 6 `--yes` | `bootstrap_pipeline.py`, `agents/director_agent.py` | argparse + `consult_user/consult_fields` |
| 7 retry budget | `core/pipeline_long.py` | `process_segment()` |
| 8 ollama client | `utils/ollama_client.py` (new) + refactors | `OllamaClient` |
| 9 degradation ledger | `agents/director_agent.py` (UIState), `core/pipeline_long.py` (`_write_manifest`), `studio_tui.py` | `add_degradation` |
| 10 world-state LLM | `memory/memory.py`, `utils/specialized_models.py` | `WorldState.update`, `extract_world_state` |
| 11 token budget | `core/pipeline_long.py`, `video/image_gen/image_gen.py` | prompt assembly + token estimate |
| 12 subtitle base | `video/renderer/assembler.py` | whisper loader |
| 13 staged loop | `core/pipeline_long.py` | segment loop / `process_segment()` |
| 14 OOM ladder | `video/image_gen/image_gen.py` | `_stable_diffusion()` + `_oom_events`/`get_oom_report` |
| 15 smooth joins | `video/renderer/assembler.py` | per-segment `afade` tuning |
| 16 thumbnail | `video/renderer/`, `core/pipeline_long.py` | post-render step + manifest |
| 17 batch mode | `bootstrap_pipeline.py` | argparse `--topics-file` loop |
| 18 music ducking | `audio/audio_fx.py` | music-mix path (`music.duck_ratio`) |
| 19 config/cleanup | `config/config.yaml`, `config/config_schemas.py` (delete) | — |
| 20 tests | `tests/` | new pytest modules + conftest |

### Key interface contracts

- `OllamaClient.generate(prompt, model, format_json=False, seed=None)`, `.chat(messages, model)`,
  `.stream(prompt, model)`. Existing `director_agent._call_ollama*` and `audio_proxy.translate_hinglish`
  become thin wrappers delegating to it — signatures unchanged.
- `UIState.add_degradation(seg, stage, reason)` appends to additive `UIState.degradations`.
- `extract_world_state(text, config) -> {characters, facts, open_threads, resolved_threads}`.

## Data Models

- **Config additions** (read via `config.get(...)`, no schema class changes) — see the corrected
  "FULL config.yaml additions" block in `plan.md`: `performance.{vram_evict_wait_s,
  vram_sd_threshold_gb, max_segment_retries, whisper_model_final, staged_loop, lookahead_segments}`,
  `ollama.{breaker_fails, breaker_cooldown_s}`, `image_gen.{lock_seed, preview_steps, oom_recovery,
  oom_min_resolution, token_budget.{identity,style,scene}}`, `audio_fx.{program_loudnorm,
  loudnorm_two_pass, target_lufs}`, `video.{audio_crossfade_ms, generate_thumbnail}`,
  `cache.cache_invented_story`, `memory.llm_world_state`, `music.{ducking, duck_ratio}`.
- **`run_manifest.json`** gains a `degradations: [{seg, stage, reason}]` array and a `thumbnail` path key.
- **`segment_NN_meta.json`** gains an `oom_events: [{tier, from_res, to_res, steps}]` array.
- **`cache/story_{topic_hash}.json`** — cached invented story (mirrors the vision-doc cache).
- **`studio_outputs/batch_report.json`** — `[{topic, status, output, degradations, wall_time_s}]`.
- **Checkpoint** gains additive sub-step keys (translation, prompts) for the staged loop.

## Correctness Properties

### Property 1: Single resident model
At no point are two Ollama models resident; evict always precedes SD.
**Validates: Requirements 1.1, 13.4**

### Property 2: Default equals legacy
With all new flags off/default, output is byte-for-byte today's behavior.
**Validates: Requirements 2.4, 3.4, 13.1**

### Property 3: Resume safety
Killing mid-run and resuming produces no duplicated work and no audio/subtitle desync.
**Validates: Requirements 13.5, 13.6**

### Property 4: API stability
`/api/status` always returns `{status, active_question, logs, output_video}`.
**Validates: Requirements 9.5**

### Property 5: Token budget preserves scene
Prompts under 77 tokens are unchanged; over-budget prompts always keep the scene text.
**Validates: Requirements 11.1, 11.2**

## Error Handling

- **VRAM never frees (Req 1):** log loud WARNING, one harder evict, proceed anyway (never hard-fail).
- **Ollama failures (Req 8):** unified backoff; circuit breaker opens after N consecutive fails, fails
  fast during cooldown, resets after `breaker_cooldown_s`.
- **World-state LLM (Req 10):** any failure/timeout/bad-JSON → fall back to the intact regex extractor.
- **OOM (Req 14):** ordered step-down ladder; final tier is black frame + recorded degradation.
- **Segment failure (Req 7):** bounded retries, then record degradation + skip, run continues.
- **Batch (Req 17):** a failing topic is logged and the batch continues to the next topic.

## Testing Strategy

- Pure-logic units (resolvers, budgeting, seed map, batch parsing, FFmpeg-command assembly)
  are unit-tested by mocking torch/urlopen/ffmpeg — no GPU or network required.
- Each phase has a GATE: `py_compile` touched files, `pytest tests/ -q` green, a PTY dry-run
  completes, and `/api/status` shape is unchanged.
- `tests/conftest.py` resets `UIState` (including the new `degradations` field) between tests.

## Compatibility & Rollback

- Every feature defaults to off (or to today's value). Turning a flag off restores prior behavior.
- The staged loop (C1) ships behind `performance.staged_loop: false`; rollback = set false.
