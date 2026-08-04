# Task Plan — Storyboard-Prompt-Builder Implementation

## Goal

Add a storyboard phase to the Video.AI pipeline: after the story outline is finalized but before expensive per-segment image generation, build a multi-panel storyboard sheet the user approves. Store per-story in memory for reuse. Optionally feed the approved sheet back as a style reference, and store per-panel shot-timing metadata for the scene director.

## Locked decisions (from user)
- Scope: Phase 1 (storyboard sheet) + shot-timing metadata. NOT full Phase 2.
- Approved sheet feeds back as style reference (gated config, default off).
- Character refs: text-derived DNA (vision_doc descriptions) + existing ProjectStore assets. NO upload path.
- Defaults: `panel_count=6`, `reference_usage=none`.

## Phase 0 — Planning files
- [x] Create task_plan.md
- [x] Create findings.md
- [x] Create progress.md

## Phase 1 — StoryStore persistence (memory/project_store.py)
- [x] Add `StoryStore.save_storyboard(storyboard: dict)` — JSON only, no schema change
- [x] Add `StoryStore.get_storyboard() -> dict | None`

## Phase 2 — Config (config/config_schemas.py + config/config.yaml)
- [x] Add `StoryboardConfig` pydantic model with `extra: forbid`
- [x] Add `"storyboard"` to `ALLOWED_KEYS` (derived from SECTION_MODELS)
- [x] Add `"storyboard": StoryboardConfig` to `SECTION_MODELS`
- [x] Add `storyboard:` section to config.yaml (enabled, panel_count, aspect, approval_retries, reuse_existing, reference_usage, inject_shot_metadata)

## Phase 3 — Prompt template (prompts.yaml)
- [x] Add `storyboard_plan` template

## Phase 4 — Core module (core/storyboard.py) — NEW
- [x] `run_storyboard(director_agent, outline, config, topic, project_name, cli_flags) -> dict | None`
- [x] Step 0: gate check (enabled / --no-storyboard)
- [x] Step 1: reuse check (StoryStore → skip if approved and not forced)
- [x] Step 2: one LLM call via `director_agent.llm._call_ollama(prompt, format_json=True)`
- [x] Step 3: parse structured per-panel JSON (beat, shot_size, camera, action, environment, dialogue, duration_sec)
- [x] Step 4: assemble panel prompts via `scene_director.assemble_prompt*`
- [x] Step 5: generate panel images via existing `generate_images`
- [x] Step 6: compose sheet via `compose_panel_pages`
- [x] Step 7: approval gate via `consult_user` (2 retries, --yes auto)
- [x] Step 8: persist via `StoryStore.save_storyboard` + sheet PNG
- [x] Step 9: return storyboard record

## Phase 5 — Pipeline hook (core/pipeline_long.py)
- [x] Call `run_storyboard()` after shape_outline, before segment closure build
- [x] Merge approved_sheet + panels into config
- [x] `attach_shot_metadata(outline, panels)` — round-robin camera/duration onto outline segments (the per-segment plan dicts)

## Phase 6 — Style reference (video/image_gen/image_gen.py)
- [x] `_stable_character_reference`: use approved sheet when `reference_usage == "direct"` (via `comfyui.storyboard_sheet` wired by the hook)

## Phase 7 — Shot metadata (utils/scene_director.py)
- [x] Read `plan["shot_metadata"]` — NO signature change (plan param already exists)

## Phase 8 — CLI (bootstrap_pipeline.py)
- [x] Add `--no-storyboard` flag
- [x] Add `--force-storyboard` flag

## Phase 9 — Tests (tests/test_storyboard.py)
- [x] Reuse skip test
- [x] Force regenerate test
- [x] Panel plan parse test
- [x] Prompt assembly test (parse + padding covered; assembly via assemble_prompt verified by parse tests)
- [x] Store/load roundtrip test
- [x] Approval flow test (--yes auto-approve; Regenerate retries max 2)
- [x] Config gating test
- [x] Shot metadata injection test (+ attach_shot_metadata round-robin tests)

## Phase 10 — Verification
- [x] `python -m pytest tests/test_storyboard.py tests/test_panel_compositor.py -q` — 32 passed
- [x] Full suite — 1994 passed, 5 skipped; `ruff check .` clean

## Phase 11 — Review
- [x] code-review (correctness) — run before commit
  - Fixed: multi-page sheet keeps all pages in record (no silent drop); UI "Proceed as planned." now approves (was looping into regenerate); garbage LLM response skips instead of raising; stale-sheet reuse regenerates; hook logs error when gate NOT applied; compose geometry mirrors _panel_sizes (layout files + page_aspect); zero-duration metadata skipped; test fakes tightened to real signatures

## Phase 12 — Close out
- [x] Update session log docs/session-2026-08-04.md
- [x] Commit + push to github-origin
