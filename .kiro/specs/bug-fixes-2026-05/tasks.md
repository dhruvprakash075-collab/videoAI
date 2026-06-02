# Implementation Plan — Bug Fixes 2026-05

## Overview

Fixes all bugs catalogued in `BUGS_AUDIT_2026-05.md` — a fresh full-codebase audit covering the Python pipeline (agents, audio, video, config, memory, utils) and the React dashboard. Bugs are verified by reading actual source; fixes are ordered lowest-blast-radius first (P4 cleanup → P3 correctness → P2 reliability/security → P1 silent features → P0 crashes). Every fix preserves existing fallbacks and keeps the pipeline importable.

Source: `BUGS_AUDIT_2026-05.md` (fresh full-codebase audit).
All bugs verified by reading actual source. Fix top-to-bottom within each wave.
After every task: `venv\Scripts\python.exe -m pytest tests/ -q` and `getDiagnostics` on changed files.

## Notes

- Unit tests must mock Ollama/OmniVoice/image-gen so no heavy GPU work runs.
- After all waves complete, run a dry-run pipeline to confirm importability: `venv\Scripts\python.exe bootstrap_pipeline.py --topic "Test" --dry-run`.
- On-hardware checks (real Hindi `--file` run: subs match audio, faces consistent, OmniVoice loads once) are manual and documented, not part of CI.
- BUG IDs in task descriptions map back to `BUGS_AUDIT_2026-05.md` for traceability.

## Tasks

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 1 — P0 crashes + P1 output-quality cluster (highest impact)
     ═══════════════════════════════════════════════════════════════ -->

- [x] 1. P0 + P1 critical output fixes
- [x] 1.1 Fix P0-1: guard `int(action)` in `consult_on_duration` against non-int/dict timeout value; default to `"keep"` on bad type
  - File: `core/pipeline_long.py`
- [x] 1.2 Fix P0-2: replace CPU `zoompan` Ken Burns with a GPU-friendly scale-based pan (or drastically reduce zoompan frame count); scale assembly timeout to realistic per-frame cost
  - File: `video/renderer/assembler.py`
- [x] 1.3 Fix P1-1: reorder exception handlers in `_stable_diffusion` so `except torch.cuda.OutOfMemoryError` comes BEFORE `except RuntimeError` to un-dead the 3-tier OOM recovery
  - File: `video/image_gen/image_gen.py`
- [x] 1.4 Fix P1-2: detect `lang: hi` and default subtitle font to a Devanagari-capable font ("Nirmala UI" on Windows, fallback "Noto Sans Devanagari") in `create_segment_mp4`
  - File: `video/renderer/assembler.py`
- [x] 1.5 Fix P1-3: use `word_timestamps_json` (and Whisper fallback) to time classic-format subtitles; remove the tiktok-only guard so timing is real for all formats
  - File: `video/renderer/assembler.py` (`_write_srt`)
- [x] 1.6 Fix P1-10: in `review_script_fast`, distinguish "reviewer unavailable" from "approved"; return `{"approved": False, "review_unavailable": True, "quality_score": 0}` instead of fabricating approval on None/exception
  - File: `utils/specialized_models.py`
- [x] 1.7 Fix P1-11: in `retry_with_backoff`, check `isinstance(e, TRANSIENT_EXCEPTIONS)` first and `continue` before the bounded check so `ConnectionError`/`TimeoutError` (OSError subclasses) get the full 50-retry transient treatment
  - File: `utils/retry_manager.py`
- [x] 1.8 Fix P1-12: in `enrich_prompts`, strip character names using the full name (not `name.split(" ")[0]`) and skip any token that is a stop-word/article ("the", "a", "an"); use full-name word-boundary regex
  - File: `utils/scene_director.py`

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 2 — P1 wiring / feature-disconnection fixes
     ═══════════════════════════════════════════════════════════════ -->

- [x] 2. P1 wiring and feature reconnection
- [x] 2.1 Fix P1-4 + P1-5: reconcile `consult_on_duration` action vocabulary to return real actions ("keep"/"cliffhanger"/"compact"/"custom"); handle "adjusted" explicitly; lock resolved user duration into DecisionRecord before conflict resolution
  - File: `core/pipeline_long.py`
- [x] 2.2 Fix P1-6: build and write a DecisionRecord on the scratch path (~line 889) and series path (~line 933), not only the adaptation path
  - File: `core/pipeline_long.py`
- [x] 2.3 Fix P1-7: normalize/whitelist the TTS engine string from the vision doc to a known engine id ("omnivoice"/"edge") before calling `tts_generate`; default to "omnivoice" for unmapped values
  - File: `core/pipeline_long.py` + `audio/audio_proxy.py`
- [x] 2.4 Fix P1-8: thread `speed`/rate into `_call_edge_direct`; map the float rate to edge-tts `rate=+X%` / `rate=-X%` format
  - File: `audio/audio_proxy.py`
- [x] 2.5 Fix P1-9: make `/api/ab/generate` return `{"job_id": ...}`; add `/api/ab/status/{job_id}` endpoint; fix frontend to clear the poll interval on terminal states
  - Files: `utils/local_ui.py`, `dashboard/src/components/ABPlayground.jsx`

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 3 — P2 security + reliability
     ═══════════════════════════════════════════════════════════════ -->

- [x] 3. P2 security and reliability
- [x] 3.1 Fix P2-1: scale Hyperframes `proc.communicate(timeout=...)` to segment duration (`max(120, int(duration*3))`); log a clear warning (not just debug) when falling back to assembler
  - File: `video/renderer/renderer.py`
- [x] 3.2 Fix P2-2: restrict CORS `allow_origins` to `["http://127.0.0.1:5173", "http://localhost:5173"]`; remove `allow_credentials=True`; add an `X-Local-Token` or `Origin` check on all mutating endpoints
  - File: `utils/local_ui.py`
- [x] 3.3 Fix P2-3: validate `topic` and `job_id` in `ab_pick` — reject path separators and `..`; resolve the target path and assert it stays under the output root
  - File: `utils/local_ui.py`
- [x] 3.4 Fix P2-4: align `run_pipeline_thread` call to `run_long_pipeline` — remove the non-existent `run_mode=` kwarg or add the parameter to the function signature
  - File: `utils/local_ui.py`
- [x] 3.5 Fix P2-5: consolidate to one schema module (`config_schema.py`); add `extra="allow"` to `ScriptConfig` and `VisualConfig`; add missing fields (`max_images_per_segment`, `environment_frame_ratio`, `dynamic_image_count`, `word_count_*`); reconcile all defaults to `config.yaml`; remove or alias `config_schemas.py`
  - Files: `config/config_schema.py`, `config/config_schemas.py`, `config/config.py`
- [x] 3.6 Fix P2-6: make `StoryMemory._save_all` write atomically (write to `.tmp` then `os.replace`); use a single module-level lock shared across instances for the same file
  - File: `memory/memory.py`
- [x] 3.7 Fix P2-7: in `PermanentMemoryLog` one-time mode, persist characters/motifs to the one-time run directory so resume works
  - File: `memory/memory.py`
- [x] 3.8 Fix P2-8: unify `train_lora` real and mock paths to emit the same diffusers-compatible safetensors key format (`to_out.0`, no `.alpha` in real path)
  - File: `train_lora.py`
- [x] 3.9 Fix P2-9: key the blackboard file by topic/run id (`blackboard_{topic_slug}.json`) instead of a single global file
  - File: `memory/blackboard.py` (and callers)
- [x] 3.10 Fix P2-10: change `ThreadPoolExecutor(max_workers=max(4, max_workers))` to `max(1, max_workers)` to respect `performance.max_workers: 1`
  - File: `core/pipeline_long.py`
- [x] 3.11 Fix P2-11: wrap `_translate_task` and `_image_prompt_task` futures in `global_scheduler.task("heavy")` and serialize with `_translation_lock`
  - File: `core/pipeline_long.py`
- [x] 3.12 Fix P2-12: include `content_text` hash in `VisionCache._key`; fix default `config_path` to `config/config.yaml` (absolute); thread `force_refresh` into `VisionCache(...)` constructor; log a warning when config path is missing
  - File: `utils/vision_cache.py` + callers
- [x] 3.13 Fix P2-13: pass `--ref-text` (from `tts.omnivoice.ref_text` config) in the OmniVoice one-shot fallback command
  - File: `audio/audio_proxy.py`
- [x] 3.14 Fix P2-14: split text chunks on sentence boundaries (`।`/`.`/`?`/`!`) not hard char count; crossfade chunk seams instead of a fixed gap
  - File: `audio/omnivoice_worker.py`
- [x] 3.15 Fix P2-15: add `normalize=0` to `amix` filter; set explicit voice/SFX volume levels
  - File: `audio/audio_fx.py`
- [x] 3.16 Fix P2-16: store the `setInterval` id in a `useRef`; clear it in a `useEffect` cleanup on unmount
  - File: `dashboard/src/components/ABPlayground.jsx`
- [x] 3.17 Fix P2-17: either add a `/api/audio/preview/{character}` endpoint and wire the Play button's `onClick`, or remove the non-functional Play button from the UI
  - Files: `utils/local_ui.py`, `dashboard/src/components/VoiceManager.jsx`

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 4 — P3 correctness / quality drift
     ═══════════════════════════════════════════════════════════════ -->

- [x] 4. P3 correctness and quality
- [x] 4.1 Fix P3-1: carry the actual TTS text (Devanagari or Hinglish) into the renderer for all paths; never pass the English `script` as subtitle source when audio is Hindi
  - File: `core/pipeline_long.py`
- [x] 4.2 Fix P3-2: include the resolved seed and LoRA file fingerprint (path + mtime) in `_prompt_cache_key`
  - File: `video/image_gen/image_gen.py`
- [x] 4.3 Fix P3-3: when no LoRA is active for the dominant character, keep the seed constant per character (remove the `+ i*7919` per-frame perturbation)
  - File: `video/image_gen/image_gen.py`
- [x] 4.4 Fix P3-4: remove `foggy`, `blurry`, `low detail` from the env-frame negative prompt; keep only anti-portrait tokens (`portrait`, `close up`, `face`, `single character`)
  - File: `video/image_gen/image_gen.py`
- [x] 4.5 Fix P3-5: extend the last image clip duration by the accumulated crossfade overlap so total video length == audio length; compute `fade_out_start` from the real post-xfade duration
  - File: `video/renderer/assembler.py`
- [x] 4.6 Fix P3-6: apply peak limiting before loudness normalization in the mastering chain
  - File: `audio/audio_fx.py`
- [x] 4.7 Fix P3-7: add `aresample` + channel-layout normalization for each SFX input before `amix`
  - File: `audio/audio_fx.py`
- [x] 4.8 Fix P3-8: read the actual sample rate from the OmniVoice model output instead of hardcoding 24000
  - File: `audio/omnivoice_worker.py`
- [x] 4.9 Fix P3-9: make `translate_hinglish` use `models.translator` (not `models.writer`); gate the Romanized-Hinglish path off when `tts.lang == "hi"` (Devanagari is preferred)
  - File: `audio/audio_proxy.py`
- [x] 4.10 Fix P3-10: compare QC duration against the sum of locked/planned segment durations (from DecisionRecord or TTS-recorded durations), not `total_duration_min * 60`
  - File: `utils/quality_check.py`
- [x] 4.11 Fix P3-11: wrap `float(fmt.get("duration", 0))` in a try/except; treat `"N/A"` or missing as unknown and record an issue instead of raising
  - File: `utils/quality_check.py`
- [x] 4.12 Fix P3-12: read `config["script"].get("max_images_per_segment", 10)` in `build_prompts` and clamp `target_count` to it instead of hardcoded 30
  - File: `utils/utils.py`
- [x] 4.13 Fix P3-13: replace `older.index(entry)` with explicit index iteration (`for idx in range(len(older)-1, -1, -1)`) in `build_context_for_prompt`
  - File: `utils/context_manager.py`
- [x] 4.14 Fix P3-14: wrap `_llm_compress` kickoff in `global_scheduler.task("heavy")`; change the shared `crewai_lock` to `threading.RLock()`
  - Files: `utils/context_manager.py`, `utils/concurrency.py`
- [x] 4.15 Fix P3-15: replace comma-count budgeting in `_cap_tokens` with a word-count estimate (`len(text.split()) * 1.3`) and a realistic cap of ~65 tokens
  - File: `utils/scene_director.py`
- [x] 4.16 Fix P3-16: add a CLIP token-count warning in `_stable_diffusion` when the prompt exceeds 77 tokens (estimate via `len(prompt.split()) * 1.3`); log the overflow so it's visible
  - File: `video/image_gen/image_gen.py`
- [x] 4.17 Fix P3-17: fix the DDG 403 retry — catch `urllib.error.HTTPError` and check `e.code == 403` instead of checking `resp.status` after `urlopen`
  - File: `utils/web_search.py`
- [x] 4.18 Fix P3-18: offload the blocking pipeline call in `run_pipeline_thread` to a background thread / `asyncio.get_event_loop().run_in_executor` so the FastAPI event loop stays responsive
  - File: `utils/local_ui.py`
- [x] 4.19 Fix P3-19: persist `uncappedScaling` in the UI config JSON; return the real saved value from the config endpoint
  - Files: `utils/local_ui.py`, `dashboard/src/components/ControlPanel.jsx`
- [x] 4.20 Fix P3-20: centralize the API base URL to `http://127.0.0.1:8000` in a shared constant or use the Vite proxy; fix `ControlPanel.jsx` which uses `localhost:8000`
  - Files: `dashboard/src/App.jsx`, `dashboard/src/components/ControlPanel.jsx`
- [x] 4.21 Fix P3-21: in `StatusTracker`, only auto-scroll when the user is already pinned to the bottom (check `scrollTop + clientHeight >= scrollHeight - threshold` before forcing scroll)
  - File: `dashboard/src/components/StatusTracker.jsx`
- [x] 4.22 Fix P3-22: correct the `%5==1` off-by-one in WorldState cadence; make proper-noun extraction Unicode-aware (use `\p{Lu}` or `\b[A-Z\u0900-\u097F]`); key continuity on full character name/id not first token
  - File: `memory/memory.py`
- [x] 4.23 Fix P3-23: remove the 24h wall-clock TTL from checkpoint `get`; expire only on explicit `clear()` or a completion flag; add a loud warning if the checkpoint is very old (>48h) but still return it
  - File: `utils/checkpoint.py`
- [x] 4.24 Fix P3-24: add `res.ok` checks in `handleScriptUpload`; reset file inputs after upload; close the consultation modal on submit; validate dropped files against `accept`/type/size in VoiceManager
  - Files: `dashboard/src/App.jsx`, `dashboard/src/components/VoiceManager.jsx`

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 5 — P4 minor / dead code / cleanup
     ═══════════════════════════════════════════════════════════════ -->

- [x] 5. P4 cleanup and minor fixes
- [x] 5.1 Fix P4-1+P4-3: fix ImportError message to not recommend xformers on Windows; unify SD load and cache-key default model names
  - File: `video/image_gen/image_gen.py`
- [x] 5.2 Fix P4-2: include `throttled_steps` (the actual steps used) in the SD cache key
  - File: `video/image_gen/image_gen.py`
- [x] 5.3 Fix P4-4+P4-5: move cleanup-manifest writes to the success path (not `finally`); wrap concat temp-file unlink in `finally`
  - File: `video/renderer/assembler.py`
- [x] 5.4 Fix P4-6: use word timestamps (when available) for Hyperframes caption `data-start`/`data-duration` in `build_html`
  - File: `video/renderer/renderer.py`
- [x] 5.5 Fix P4-7+P4-8+P4-9: fix audio_fx docstring; document SFX map status; align `sentence_gap_ms` default to config (200ms) and consume it in the worker
  - Files: `audio/audio_fx.py`, `audio/audio_proxy.py`, `audio/omnivoice_worker.py`
- [x] 5.6 Fix P4-10+P4-11: align in-code TTS engine/num_step defaults to config values; make reference voice selection deterministic (prefer `narration_voice.wav`, exclude `*_ref8s_mono*` cache files)
  - File: `audio/audio_proxy.py`
- [x] 5.7 Fix P4-12: fix image-prompt colon truncation — only strip a leading label when it matches a known prefix (`^prompt\s*:` / `^output\s*:`), not any colon in the first 50 chars
  - File: `utils/specialized_models.py`
- [x] 5.8 Fix P4-13+P4-14+P4-15: remove dead `_is_ollama_available`/`_get_ollama_host` helpers and unused `urllib.request` import from scene_director; de-dupe `"premise"` in `_WIKI_INCLUDE_SECTIONS`; remove unused `import urllib.error`; fix DDG dedup to use `(title, summary)` when URL is empty
  - Files: `utils/scene_director.py`, `utils/web_search.py`
- [x] 5.9 Fix P4-16+P4-17: fix `story_planner` `words_per_segment` fallback to 130; fix `_default_outline` to build `char_presence` with exactly `num_images` entries and seed one low-weight env frame
  - File: `utils/story_planner.py`
- [x] 5.10 Fix P4-18+P4-19: make heavy scheduler wait timeout configurable / much larger (default 1800s); clean up orphaned `.bak`/`.tmp`/`.corrupt.*` siblings in `checkpoint.clear`
  - Files: `utils/concurrency.py`, `utils/checkpoint.py`
- [x] 5.11 Fix P4-20+P4-21+P4-22+P4-23+P4-24+P4-25: fix Devanagari ellipsis doubling; remove dead ExecutiveAgent/create_executive; fix stale `_last_segment_count` in `est_duration`; fix cuDNN no-op block; guard dry-run duration format; validate music track path with `.exists()`
  - Files: `utils/emotion_control.py`, `agents/executive_agent.py`, `core/main.py`, `core/pipeline_long.py`
- [x] 5.12 Fix P4-27+P4-28+P4-29+P4-30+P4-31+P4-32+P4-33: fix `run_pipeline.py` hardcoded path; replace deprecated `torch.cuda.amp.autocast`; clean up compatibility double-patch and stale warning filters; fix `_safe_filename` to preserve Devanagari; fix frontend a11y (alt, keys, labels, AbortController); fix bool-as-int check; fix CLI flag mislabel
  - Files: `run_pipeline.py`, `core/pipeline_long.py`, `utils/compatibility.py`, `memory/project_store.py`, `dashboard/src/components/*.jsx`
- [x] 5.13 Delete `_bugtest.py` scratch file from repo root
  - File: `_bugtest.py`

<!-- ═══════════════════════════════════════════════════════════════
     WAVE 6 — Full verification
     ═══════════════════════════════════════════════════════════════ -->

- [x] 6. Full verification
- [x] 6.1 Run full test suite; confirm no heavy GPU/model calls in unit tests (all mocked); fix any test failures introduced by the fixes
  - Command: `venv\Scripts\python.exe -m pytest tests/ -v`
- [x] 6.2 Run `getDiagnostics` on all changed Python files; resolve all errors and warnings
- [x] 6.3 Verify pipeline is importable end-to-end: `venv\Scripts\python.exe -c "from core.pipeline_long import run_long_pipeline; print('OK')"`
- [x] 6.4 Verify dashboard builds cleanly: `cd dashboard && npm run build && npm run lint`

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"], "rationale": "P0 crashes and P1 output-quality — highest impact, unblock everything" },
    { "wave": 2, "tasks": ["2"], "rationale": "P1 wiring fixes depend on wave 1 (retry, schema, pipeline structure)" },
    { "wave": 3, "tasks": ["3"], "rationale": "P2 security/reliability — depends on schema fix (3.5) from wave 2" },
    { "wave": 4, "tasks": ["4"], "rationale": "P3 correctness — builds on the fixed pipeline/audio/memory from waves 1-3" },
    { "wave": 5, "tasks": ["5"], "rationale": "P4 cleanup — safe to do last, no behavior risk" },
    { "wave": 6, "tasks": ["6"], "rationale": "Full verification after all fixes land" }
  ]
}
```
