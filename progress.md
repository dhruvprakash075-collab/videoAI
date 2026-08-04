# Progress — Storyboard-Prompt-Builder Implementation

## Session: 2026-08-04 (night)

### Phase 0 — Planning files
- **Status**: complete
- Created `task_plan.md` with full 12-phase plan
- Created `progress.md` (this file)
- Created `findings.md` (audit results)

### Key decisions
- `panel_count` default: 6 (framework default)
- `reference_usage` default: none (off — safest)
- Hook point: `core/pipeline_long.py` after `shape_outline`, before segment closure build
- LLM access: `director_agent.llm._call_ollama(prompt, format_json=True)`
- Sheet-as-reference targets `_stable_character_reference` (not `_reference_pool`), wired via `image_gen.comfyui.storyboard_sheet`
- No `enrich_prompts` signature change — shot metadata rides in existing `plan` dict (which IS the outline segment, `segment_runner._build_segment_state`); `attach_shot_metadata()` round-robins panels onto outline segments
- Sheet PNG lands in `studio_outputs/{topic}/storyboard/` (consistent with rest of pipeline media; StoryStore record stores the path)

## Session: 2026-08-04 (late night) — implementation

### What was done
- All 8 modules touched per plan (see git diff): storyboard.py NEW, pipeline_long.py hook, project_store.py save/get_storyboard, config.yaml + config_schemas.py StoryboardConfig, prompts.yaml storyboard_plan, image_gen.py sheet override in `_stable_character_reference`, scene_director.py shot_metadata read, bootstrap_pipeline.py --no-storyboard/--force-storyboard
- Gap found + fixed: shot metadata was only consumed (enrich_prompts) but never written — added `attach_shot_metadata()` in storyboard.py + hook call + 2 tests
- Tests: `tests/test_storyboard.py` — 16 tests (reuse skip, missing-sheet regen, force regen, parse, padding, roundtrip, approval yes, proceed-default approves, garbage-LLM skips, retries, config gating, flag gating, metadata inject on/off, attach round-robin, no-panels)

### Code review (agent) — 5 bugs + 4 nits found, all fixed
- Multi-page sheet: record now keeps `sheet_pages` (all pages), primary = pages[0] — no silent drop of panels >5
- UI-mode "Proceed as planned." used to loop into regenerate; approval logic inverted to "any non-regenerate reply approves"
- Garbage LLM JSON raised out of run_storyboard (extract_json raises ValueError) → now caught, storyboard skipped; per-field duration float hardened
- Stale reuse: approved record with deleted sheet PNG → regenerates instead of reusing dead path
- Hook catch-all now logs ERROR "gate NOT applied" (was silent warning)
- Compose geometry now mirrors image_gen._panel_sizes (layout files + page_aspect from panel_composite config) so generation sizes == sheet rects
- Nit fixes: pad cap (12) removed → exact panel_count; zero-duration metadata skipped; test fakes tightened to real signatures

### Verification
- `pytest tests/test_storyboard.py tests/test_panel_compositor.py -q` → 32 passed
- Full suite → 1997 passed, 5 skipped
- `ruff check .` → clean

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | - | - |

## Session: 2026-08-05 - hardening pass

### What was done (4 fixes, commit 18245df6e)
- **MAJOR dry-run bug**: storyboard hook now gated `if not (dry_run or fast_dry_run)` - dry runs did real LLM calls + real ComfyUI generations, composed the sheet, PERSISTED an approved record that the real run silently reused (finalize_dry_run exits AFTER the hook). Regression test: `test_storyboard_gate_skipped_on_dry_run`.
- **Hook wiring extracted + tested**: new `wire_storyboard(config, outline, storyboard)` in storyboard.py (config approved_sheet/panels + storyboard_sheet + outline shot_metadata, idempotent no-op on None); 2 direct unit tests (merge + noop).
- **Regenerate now uses feedback**: on Regenerate, asks "What should change in the storyboard plan?" (allow_custom=True); non-default replies appended as `USER FEEDBACK (address it in the revised plan): ...` to the next attempt's prompt; unattended defaults ("Proceed as planned."/"Proceed with default settings.") filtered; prior feedback persists across retries.
- **Dead config removed**: `storyboard.aspect` deleted from StoryboardConfig + config.yaml (was only read when panel_composite disabled - a lie); fallback branch now fixed 16:9 constant, `_page_aspect_from_config` deleted.

### Verification
- `pytest tests/test_storyboard.py tests/test_pipeline_long.py tests/test_config.py` - 78 passed
- Full suite - 1937 passed, 5 skipped (excl. node tests) + 71 node tests passed
- `ruff check .` - clean
- Committed + pushed to github-origin main (18245df6e)
