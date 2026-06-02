# Implementation Plan

## Overview

Fixes the production-phase defects from `BUGS.md` in five workstreams, ordered
lowest-blast-radius first (originality/config purges), then subtitle correctness,
audio reconnection, visual identity, reliability, and finally the OmniVoice persistent
worker (largest change, behind a fallback). Every fix preserves existing fallbacks so a
long run never hard-crashes, and text-stage fixes add no heavy GPU calls.

## Tasks

- [ ] 1. Originality purge (no IP terms anywhere)
- [ ] 1.1 Replace named characters in `config/config.yaml` and `config/config.py` `_default_config` with original placeholders; remove franchise keywords
  - _Requirements: 7.1_
- [ ] 1.2 Change code fallbacks to generic names: `utils/utils.py` (`build_prompts`), `utils/local_ui.py` (`upload_voice`), `agents/executive_agent.py` (`execute_voice_over`), `agents/director_agent.py` (`UIState.character`)
  - _Requirements: 7.1, 7.2_
- [ ] 1.3 Neutralize the image-engineer few-shot example in `utils/specialized_models.py`; update the docstring example in `memory/memory.py`
  - _Requirements: 7.3_
- [ ] 1.4 Update test fixtures in `tests/test_project_store.py` to use original placeholder names
  - _Requirements: 7.5_
- [ ] 1.5 Add originality gating: when run_mode is original/one-time, skip franchise web research in `utils/web_search.py`
  - _Requirements: 7.4_
- [ ] 1.6 Add a test asserting defaults/fallbacks contain no franchise terms
  - _Requirements: 7.1_

- [ ] 2. Config reconciliation and cleanup
- [ ] 2.1 Align `words_per_segment` default across `config_schema.py`, `config_schemas.py`, `config.yaml`
  - _Requirements: 11.1_
- [ ] 2.2 Align OmniVoice fallback defaults in `audio/audio_proxy.py` with `config.yaml`
  - _Requirements: 11.2_
- [ ] 2.3 Fix Wikipedia include/skip section matching to exact (no substring conflicts) in `utils/web_search.py`
  - _Requirements: 11.3_
- [ ] 2.4 Fix `_prompt_cache_key` defaults in `image_gen.py` to match real generation params
  - _Requirements: 8.3_
- [ ] 2.5 Remove dead constants/branches (`create_executive` model, `urlopen` retry branch, `_CHROME_PATH`, `_DDG_API`) and trim SFX map to existing files
  - _Requirements: 11.4, 11.5_
- [ ] 2.6 Make the quality check compare against resolved decision-record duration, not raw config
  - _Requirements: 8.4_

- [ ] 3. Subtitle correctness
- [ ] 3.1 In `core/pipeline_long.py`, pass the TTS script (Devanagari when hi) and `word_timestamps_json` to `render_with_assets`
  - _Requirements: 1.1, 1.2_
- [ ] 3.2 Extend `render_with_assets`/`build_html` signatures to accept `subtitle_script` and `word_timestamps_json`; thread timestamps to assembler fallback
  - _Requirements: 1.2, 1.3_
- [ ] 3.3 Use a Devanagari-capable caption font (Noto Sans Devanagari + fallback) in `build_html`
  - _Requirements: 1.4_
- [ ] 3.4 Build captions from word timestamps when present; else proportional
  - _Requirements: 1.3_
- [ ] 3.5 Tests: subtitle source text equals TTS text for a hi run; missing-timestamp fallback path
  - _Requirements: 1.1, 1.3, 1.5_

- [ ] 4. Audio chain reconnection
- [ ] 4.1 Add Devanagari-aware emotion shaping to `utils/emotion_control.py` (`।`/`?`/`!` boundaries; `lang` param)
  - _Requirements: 2.1_
- [ ] 4.2 Fix pipeline order: translate to Devanagari, THEN `inject_emotion(devanagari, mood, lang="hi")`, use as `script_for_tts` (remove the overwrite)
  - _Requirements: 2.1_
- [ ] 4.3 Plumb `get_mood_rate(mood)` into `tts_generate(..., speed=...)` and through to the OmniVoice `--speed`
  - _Requirements: 2.2, 2.3_
- [ ] 4.4 Add fallbacks: emotion/rate failure → plain script + default speed, logged
  - _Requirements: 2.4_
- [ ] 4.5 Add reference-audio validator (5–15s mono; trim/warn) used once per run
  - _Requirements: 6.2_
- [ ] 4.6 Strengthen `translate_to_devanagari` loanword/number rules; add `_devanagari_ratio` post-check with bounded re-translation + warning
  - _Requirements: 2.6, 2.7_
- [ ] 4.7 Tests: Devanagari emotion boundaries; Latin path unchanged; mood-rate passed to TTS (mock); Devanagari-ratio check flags Latin-heavy text
  - _Requirements: 2.1, 2.2, 2.6, 2.7_

- [ ] 5. Visual identity (face consistency)
- [ ] 5.1 Add per-frame seed support to `_stable_diffusion` (use visual-lock seed or deterministic char-key fallback; build `torch.Generator`)
  - _Requirements: 3.1, 3.6_
- [ ] 5.2 Add a token-budgeted prompt assembler (identity-first, trim boilerplate, ~70-token cap) in `utils/scene_director.py`; use it in pipeline assembly
  - _Requirements: 3.2, 3.3_
- [ ] 5.3 Remove the `photorealistic, masterpiece` positive tokens that contradict the negative prompt
  - _Requirements: 3.4_
- [ ] 5.4 Ensure `num_images` from the plan flows unmodified to `generate_images`
  - _Requirements: 3.5_
- [ ] 5.5 Wire visual-lock seed read from ProjectStore into the pipeline image call
  - _Requirements: 3.1_
- [ ] 5.6 In the prompt assembler, prioritize world/environment tokens for low-presence (environmental) frames and minimize identity tokens there
  - _Requirements: 3.7_
- [ ] 5.7 Tests: deterministic seed (same key → same seed; stored seed used); prompt assembler keeps identity within budget; environmental frame keeps env detail
  - _Requirements: 3.1, 3.2, 3.6, 3.7_

- [ ] 6. Reliability hardening
- [ ] 6.1 Split retry exceptions in `utils/retry_manager.py`: transient (50×) vs deterministic (≤3); remove dead `urlopen` branch
  - _Requirements: 4.1, 4.2, 4.4_
- [ ] 6.2 Stop double-wrapping `generate_images` (it has internal OOM tiers) — outer retry only for transient
  - _Requirements: 4.3_
- [ ] 6.3 Acquire the shared CrewAI lock in `context_manager._llm_compress`; timeout → non-LLM summary fallback
  - _Requirements: 5.1, 5.2_
- [ ] 6.4 Make Hyperframes renderer env-driven (WSL distro/user/path) with availability detection in `video/renderer/renderer.py`
  - _Requirements: 9.1, 9.2, 9.3_
- [ ] 6.5 Fix `review_script_fast` to use brace-depth JSON extraction
  - _Requirements: 10.1, 10.2_
- [ ] 6.6 Tests: transient vs deterministic retry counts; generate_images not double-wrapped; reviewer nested-JSON parse; renderer env resolution
  - _Requirements: 4.2, 4.3, 5.1, 10.1_

- [ ] 7. OmniVoice performance
- [ ] 7.1 Add a persistent-worker mode to `audio/omnivoice_worker.py` (load model once, read line-delimited JSON requests from stdin, emit JSON responses)
  - _Requirements: 6.1_
- [ ] 7.2 Update `audio/audio_proxy.py` to keep the worker alive across segments and pipe requests; fall back to per-call subprocess if it fails to start
  - _Requirements: 6.1, 6.4_
- [ ] 7.3 Tune `audio_chunk_threshold` for segment length and handle chunk seams for voice-clone timbre
  - _Requirements: 6.3_
- [ ] 7.4 Keep OmniVoice preferred with edge-tts fallback intact
  - _Requirements: 6.5_
- [ ] 7.5 Tests: worker fallback path (mock subprocess); speed override reaches worker args
  - _Requirements: 6.1, 6.4_

- [ ] 8. Resolution / YouTube quality (needs VRAM decision — see Open Questions)
- [ ] 8.1 Implement the chosen resolution strategy (native higher-res or upscale pass) for 1080p output
  - _Requirements: 8.1, 8.2_
- [ ] 8.2 Preserve existing NVENC encoder tuning
  - _Requirements: 8.5_
- [ ] 8.3 Tests/verification: output resolution matches target; cache keys consistent
  - _Requirements: 8.3_

- [ ] 10. Model-switch readiness (image + TTS)
- [ ] 10.1 Refactor `tts_generate` to dispatch via an engine registry (`_TTS_ENGINES`); adding an engine = one adapter, no per-segment flow change
  - _Requirements: 12.3_
- [ ] 10.2 Audit `_stable_diffusion` for config-purity: steps, guidance, scheduler, width, height, negative prompt all from config; make scheduler config-selectable
  - _Requirements: 12.1, 12.2_
- [ ] 10.3 Add active model id (`sd_model_path`) to the SD cache key so switching models invalidates stale cache
  - _Requirements: 12.4_
- [ ] 10.4 Add capability profiles (`tts_capabilities()` + image-model profile table) documenting cloning support, languages, VRAM hints, recommended settings
  - _Requirements: 12.5_
- [ ] 10.5 Add opt-in eval harness `utils/model_eval.py` + `--eval-models` flag: generate a small fixed-prompt image set and one short TTS clip for A/B, without a full video
  - _Requirements: 12.6_
- [ ] 10.6 Unavailable model/engine → clear error + documented default fallback (no mid-run crash)
  - _Requirements: 12.7_
- [ ] 10.7 Add an acceleration-adapter config slot (`image_gen.acceleration`: dmd2/hyper_sd/lcm/none) with step/guidance/sampler overrides; default none; DMD2 as first candidate
  - _Requirements: 12.8_
- [ ] 10.8 Add a config-selectable upscaler (`image_gen.upscaler`: 4x-UltraSharp/realesrgan/none), tiled for 6GB; generate small then upscale to 1080p
  - _Requirements: 12.9, 8.1, 8.2_
- [ ] 10.9 Document candidate registry + capability profiles for evaluation: alternative SD 1.5 checkpoint(s); IndicF5 / OpenVoice v2 as Hindi TTS candidates
  - _Requirements: 12.10_
- [ ] 10.10 Tests: engine registry routing; cache key changes with model id; eval harness writes samples (mock generation); acceleration slot applies overrides; unavailable-engine fallback
  - _Requirements: 12.1, 12.3, 12.4, 12.7, 12.8_

- [ ] 11. Operator preview & approval gate
- [ ] 11.1 Add `--preview` flag (and API field); after the first segment's sample images + short narration clip, pause via the existing UIState/Director-Mode mechanism and surface sample paths
  - _Requirements: 13.1, 13.2, 13.6_
- [ ] 11.2 On approve → continue with same settings; on reject → stop without full production, leaving samples
  - _Requirements: 13.3_
- [ ] 11.3 Configurable timeout with documented attended-default (proceed) vs safe-default (abort); off by default
  - _Requirements: 13.4, 13.5_
- [ ] 11.4 Tests: preview pauses before full production; reject stops; off-by-default proceeds unattended (mock approval)
  - _Requirements: 13.2, 13.3, 13.4_

- [ ] 12. Production-time benchmark
- [ ] 12.1 Add a benchmark utility that records seconds-per-image and seconds-per-segment, runnable before/after enabling optional changes
  - _Requirements: 12.8_
- [ ] 12.2 Capture baseline vs acceleration-adapter vs upscaler timings; write a small report for operator review
  - _Requirements: 12.8, 12.9_

- [ ] 9. Full verification
- [ ] 9.1 Run the unit suite; assert no heavy GPU/model calls in unit tests (mocks)
  - _Requirements: 2.5_
- [ ] 9.2 Dry-run a hi `--file` pipeline; confirm subtitle text equals TTS text, decision record honored, no IP terms in output config
  - _Requirements: 1.1, 7.1_
- [ ] 9.3 Confirm Correctness Properties 1–12 via the test matrix; clean up temp artifacts
  - _Requirements: all_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2"], "rationale": "Pure replacements/cleanup — no behavior risk, unblock everything" },
    { "wave": 2, "tasks": ["3", "4"], "rationale": "Subtitle correctness and audio reconnection (incl. Devanagari loanwords) are independent text-stage fixes" },
    { "wave": 3, "tasks": ["5", "6", "10"], "rationale": "Visual identity (incl. env imagery), reliability, and model-switch readiness build on the cleaned base" },
    { "wave": 4, "tasks": ["7", "11"], "rationale": "OmniVoice persistent worker and the operator preview gate (preview reuses the HITL mechanism)" },
    { "wave": 5, "tasks": ["8", "12"], "rationale": "Resolution strategy + production-time benchmark — need the VRAM decision and the optional changes in place" },
    { "wave": 6, "tasks": ["9"], "rationale": "Full verification after all fixes land" }
  ]
}
```

Visual overview:

```
1 (originality) ─┐
2 (config)       ├──────► 3 (subtitles) ─┐
                 └──────► 4 (audio)       ├─► 5 (visual identity) ─┐
                                          └─► 6 (reliability)      ├─► 9 (verify)
                          7 (omnivoice) ──────────────────────────┤
                          8 (resolution, after VRAM decision) ─────┘
```

Critical path: 1 → 3 → 4 → 5 → 9 (the output-quality cluster).

## Notes

- After every task, run the relevant unit tests and `getDiagnostics` on changed files;
  keep the pipeline importable.
- Unit tests must mock Ollama/OmniVoice/image-gen so no heavy GPU work runs; some tests
  assert zero heavy calls (Performance, R2.5).
- Tasks 1–2 are pure replacements/cleanup (no behavior change). Tasks 3–6 are
  text-stage/reliability fixes. Task 7 is behind a fallback. Task 8 waits on the
  resolution VRAM decision.
- Reuse the prior spec's artifacts: `DecisionRecord` (run_mode, durations) and
  `ProjectStore.visual_locks` (seed). Do not duplicate them.
- BUG IDs in the design map back to `BUGS.md` for traceability.
- On-hardware checks (real Hindi `--file` run: subs match audio, faces consistent,
  OmniVoice loads once, crisp 1080p) are manual and documented, not part of CI.

## Open Questions (resolve before Wave 5)

1. Resolution strategy (B3): 960×540 + upscale to 1080p vs native — pending a VRAM
   measurement on the RTX 4050.
2. OmniVoice worker protocol: line-delimited JSON over stdin/stdout (proposed) vs local
   socket.
3. Devanagari caption font: bundle Noto Sans Devanagari (proposed) vs system font.
