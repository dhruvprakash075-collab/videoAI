# Implementation Plan

## Overview

This plan implements the unified Director structural-decision authority: a single
versioned `DecisionRecord` on a shared blackboard that the Director, Writer, user, and
runtime all use; real length controls (cliffhanger/compaction); three-tier
project/story/audit memory; per-character visual locks; and unification of the CLI and
dashboard flows. Tasks are ordered additive-first so the codebase stays runnable after
every step, with the risky pipeline switch (Task 7) landing only after the scaffolding
is in place and tested.

## Tasks

- [ ] 1. Add the DecisionRecord data model and helpers
- [ ] 1.1 Extend `config/config_schemas.py` with `Provenance`, `Decision`, `PerSegmentOverride`, `DecisionRecord`, and `DECISION_SCHEMA_VERSION`
  - Use `default_factory` for all list/dict fields; construct `Decision` defaults per-record
  - Reuse existing range bounds (words 50–800, images 1–30, segment count bounded)
  - _Requirements: 1.1, 1.2, 1.3_
- [ ] 1.2 Implement `DecisionRecord.set(field, value, provenance, lock=False)` enforcing lock immutability (only `user`/`cli_flag` may relock)
  - _Requirements: 3.1, 3.2_
- [ ] 1.3 Implement `resolve_conflicts()` (locked-wins, both-locked-raises `DecisionConflict`, else prefer `segment_count` and log)
  - _Requirements: 3.3, 4.2_
- [ ] 1.4 Implement `to_overlay()` (flatten to current overlay shape) and `provenance_report()`
  - _Requirements: 1.5, 17.1_
- [ ] 1.5 Implement validation fallback: invalid field → documented default + `provenance="default"` + warning, no crash
  - _Requirements: 1.4_
- [ ] 1.6 Write unit tests for set/lock, resolve_conflicts (all branches), validation clamps, to_overlay round-trip
  - _Requirements: 1.3, 1.4, 3.1, 3.2, 3.3_

- [ ] 2. Add schema versioning and migration
- [ ] 2.1 Add `version` field and `load_decision_record(raw)` that migrates old → current, else rebuilds from config
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_
- [ ] 2.2 Implement `migrate_decision_record(raw, from_version)` with ordered, idempotent steps; `build_default_decision_record(config)`
  - _Requirements: 16.3, 16.4_
- [ ] 2.3 Unit tests: v0 (no version) record migrates; unmigratable record rebuilds without crashing
  - _Requirements: 16.3, 16.4_

- [ ] 3. Add the Blackboard persistence layer
- [ ] 3.1 Create `memory/blackboard.py` with `read/write/read_decision/write_decision`, atomic temp-file + `os.replace`, in-process `RLock`
  - _Requirements: 10.1, 10.2, 10.3, 10.5_
- [ ] 3.2 Add optional cross-process guard via `filelock` if importable; safe no-op fallback otherwise
  - _Requirements: 10.4_
- [ ] 3.3 Unit tests: atomic write (no partial file on simulated failure), concurrent writers serialize, read with no model loaded
  - _Requirements: 10.3, 10.4_

- [ ] 4. Add the DecisionEngine
- [ ] 4.1 Create `agents/decision_engine.py` with `build_decision_record(director, vision_doc, user_locks, cli_flags, config)` implementing seed→director→writer→user/cli→resolve
  - _Requirements: 1.1, 2.1, 2.5, 3.4_
- [ ] 4.2 Implement explicit Writer consent: pass Director's proposed numbers into the Writer consult; capture agreement/adjustment + rationale with provenance
  - _Requirements: 2.2, 2.3, 14.1, 14.2, 14.4, 14.5_
- [ ] 4.3 Map `--duration` and other CLI flags to `cli_flag` locks; map typed user consultation answers to `user` locks (no substring inference)
  - _Requirements: 3.5, 3.6_
- [ ] 4.4 Unit tests: writer adjustment recorded with provenance; user lock survives later director/writer writes; cli_flag locks duration
  - _Requirements: 2.5, 3.1, 3.2, 14.2_

- [ ] 5. Implement real length controls (replace stubs)
- [ ] 5.1 Implement `DirectorAgent.suggest_cliffhangers(content, current_minutes)` to return ≥2 high-note end points (one pre-production LLM call)
  - _Requirements: 6.5_
- [ ] 5.2 Implement `DirectorAgent.compact_story(content, target_minutes, original_minutes)` to condense source to target length (one pre-production LLM call)
  - _Requirements: 6.4_
- [ ] 5.3 Write cliffhanger/compaction outcomes into the DecisionRecord (`end_mode`, `cliffhanger_point`, adjusted duration) with provenance `user`
  - _Requirements: 6.8_
- [ ] 5.4 Make duration negotiation available on `--topic` (use Director estimate) and `--file`; skip when duration is locked
  - _Requirements: 6.1, 6.2, 6.3, 6.6, 6.7_
- [ ] 5.5 Unit tests (mock LLM): cliffhanger returns ≥2 options; compaction targets requested minutes; locked duration skips negotiation
  - _Requirements: 6.4, 6.5, 6.6_

- [ ] 6. Wire pre-production to build and persist the DecisionRecord
- [ ] 6.1 In `run_pre_production`, call `DecisionEngine`, build the record, persist via `CheckpointManager`, and still emit today's overlay via `to_overlay()` (behavior parity)
  - _Requirements: 1.5, 5.5_
- [ ] 6.2 Ensure cliffhanger/compact duration is applied as a `user` lock BEFORE `resolve_conflicts`, so it is never overwritten (fixes the clobber bug)
  - _Requirements: 3.2, 6.8_
- [ ] 6.3 Pass `project_name` into `load_config()` so `projects/{name}.yaml` actually loads
  - _Requirements: 8.3_
- [ ] 6.4 Integration test (dry-run, mock LLM): record persisted; overlay parity with prior behavior for a no-lock run
  - _Requirements: 1.5_

- [ ] 7. Make the runtime obey the DecisionRecord (the core fix)
- [ ] 7.1 In `run_long_pipeline`, read `segment_count`/`words_per_segment` from the record instead of re-deriving `n_segs` from duration
  - _Requirements: 4.1, 4.2_
- [ ] 7.2 Pass authoritative `words_per_segment` into `story_planner.plan_story`; keep per-segment `target_word_count` within ±25% band; honor locked per-segment values
  - _Requirements: 4.3, 4.4_
- [ ] 7.3 Log provenance of the segment count and word targets actually used
  - _Requirements: 4.5_
- [ ] 7.4 Integration test: `n_segs == rec.segment_count.value`; locked duration+segment conflict surfaces instead of silently resolving
  - _Requirements: 3.3, 4.1_

- [ ] 8. Word-count enforcement per segment
- [ ] 8.1 Extend `validate_script` / segment flow to measure actual vs target words and apply bounded correction (regen/trim/pad), text-stage only
  - _Requirements: 15.1, 15.2, 15.3, 15.6_
- [ ] 8.2 Tightest tolerance for user-locked per-segment values; read tolerance/retries from config (defaults ±25%, 2)
  - _Requirements: 15.4, 15.5_
- [ ] 8.3 Unit tests: deviation triggers bounded retries; mock image-gen asserts zero heavy calls; converges or accepts best + logs
  - _Requirements: 15.3, 15.6_

- [ ] 9. Three-tier memory (project / story / audit)
- [ ] 9.1 Refactor `PermanentMemoryLog` into `ProjectStore` + `StoryStore` under `studio_projects/{project}/...`, preserving the existing get/log/check API
  - _Requirements: 12.1, 12.2, 12.5_
- [ ] 9.2 One-time-use runs write only to an isolated story store; never touch `project.json`
  - _Requirements: 9.3, 12.3_
- [ ] 9.3 Backward-compat shim: load legacy `studio_checkpoints/{topic}_memory.json` as a one-time story store when no project store exists
  - _Requirements: 9.1, 12.4_
- [ ] 9.4 Unit tests: project vs story isolation; one-time run leaves project.json untouched; legacy file loads
  - _Requirements: 9.3, 12.2, 12.3_

- [ ] 10. Per-character visual lock
- [ ] 10.1 Store visual lock (description + seed/LoRA ref) in `project.json`; reuse across stories in the project
  - _Requirements: 13.1, 13.3, 13.4_
- [ ] 10.2 Apply stored visual lock during image-gen (continue description-injection + LoRA face-lock); sparse description → skip + log, no failure
  - _Requirements: 13.2, 13.5_
- [ ] 10.3 Unit tests: lock persisted with provenance; reused across stories; sparse-description skip path
  - _Requirements: 13.1, 13.5_

- [ ] 11. Run mode + flags
- [ ] 11.1 Add `--run-mode {project|one_time}` CLI flag and matching API form field; document default (one_time) and log it
  - _Requirements: 9.1, 9.4, 9.5_
- [ ] 11.2 Route persistence by run mode (project → persistent stores; one_time → isolated)
  - _Requirements: 9.2, 9.3_
- [ ] 11.3 Preserve existing flags (`--duration`→cli_flag lock, `--project`→loads overrides, `--series`, `--director-mode`)
  - _Requirements: 9.4 (compat)_

- [ ] 12. Unify the dashboard onto the shared pathway
- [ ] 12.1 Change `utils/local_ui.run_pipeline_thread` to call `run_pre_production` + `run_long_pipeline` (pass upload as `content_text`), keeping UIState status/pause/log hooks
  - _Requirements: 5.1, 5.2, 5.4_
- [ ] 12.2 Reduce `define_pacing_and_length` to a shim delegating to the DecisionEngine (or remove if unused); construct `DirectorAgent` with full config (not just models)
  - _Requirements: 5.3, 5.5_
- [ ] 12.3 Integration test: dashboard path produces same DecisionRecord structure as CLI for equivalent input
  - _Requirements: 5.5_

- [ ] 13. Provenance report in the run manifest
- [ ] 13.1 Extend `_write_manifest` with a `decisions` block: per-field value+provenance+locked, resolved values, and adjustments (clamps/conflicts/word-count corrections)
  - _Requirements: 17.1, 17.2, 17.3, 17.4_
- [ ] 13.2 Ensure manifest generation stays end-of-run with no model calls
  - _Requirements: 17.5_
- [ ] 13.3 Unit test: manifest contains expected decision fields and an adjustments entry when a clamp occurs
  - _Requirements: 17.1, 17.2_

- [ ] 14. Backward compatibility and resume
- [ ] 14.1 When config has no decision record, build one from existing config values (provenance default/cli_flag) and continue
  - _Requirements: 7.1_
- [ ] 14.2 On resume, reload persisted decision record and do not re-prompt unless asked
  - _Requirements: 7.2_
- [ ] 14.3 Fallback to documented defaults when Ollama/web search unavailable
  - _Requirements: 7.4, 9.4_
- [ ] 14.4 Integration test: legacy config run; resume run reuses decisions; offline fallback
  - _Requirements: 7.1, 7.2, 7.4_

- [ ] 15. Risk-tiered intervention
- [ ] 15.1 Drive intervention points from the existing impact ranking: high-impact structural decisions prompt/lock; low-impact decided silently by Director
  - _Requirements: 11.1, 11.2, 11.3, 11.4_
- [ ] 15.2 Non-interactive/timeout → high-impact decisions fall back to Director/Writer-agreed values and log auto-proceed
  - _Requirements: 11.5_
- [ ] 15.3 Unit test: high-impact gate offered; low-impact auto-proceeds; timeout path logs and proceeds
  - _Requirements: 11.2, 11.3, 11.5_

- [ ] 16. Full verification
- [ ] 16.1 Run the full unit suite; ensure no heavy GPU/model calls are invoked in unit tests (mocks assert this)
  - _Requirements: 15.6, Performance_
- [ ] 16.2 Run a CLI `--topic --dry-run` end-to-end with Ollama up; confirm decision record built, runtime honors it, manifest shows provenance
  - _Requirements: 4.1, 4.5, 17.1_
- [ ] 16.3 Confirm Correctness Properties 1–9 hold via the test matrix; clean up any temp artifacts
  - _Requirements: all_

## Code Anchors (for the implementer)

Exact locations as of spec authoring (line numbers may drift — match on the symbol).

New files to create:
- `memory/blackboard.py` — `Blackboard` class (Task 3).
- `agents/decision_engine.py` — `build_decision_record(...)` (Task 4).
- Tests under `tests/` (create the dir if absent; project has no test runner yet —
  set up `pytest` as the standard choice and add it to `requirements.txt`).

Files to edit, with anchors:
- `config/config_schemas.py` — append `Provenance`, `Decision`, `PerSegmentOverride`,
  `DecisionRecord`, `DECISION_SCHEMA_VERSION`, `load_decision_record`,
  `migrate_decision_record`, `build_default_decision_record` (Tasks 1, 2). Also export
  the new symbols in `config/__init__.py`.
- `core/pipeline_long.py`:
  - `run_long_pipeline` starts at line ~832; `config = load_config()` at ~856 →
    pass `project_name` (Task 6.3).
  - `n_segs = max(1, -(-total // seg_min))` at ~893 → read `rec.segment_count.value`
    (Task 7.1).
  - `words_per_seg = config.get("script", {}).get("words_per_segment", 390)` at ~897 →
    read `rec.words_per_segment.value` (Task 7.1).
  - `seg_words = plan.get("target_word_count", words_per_seg)` at ~1027 → keep within
    ±25% band; honor per-segment lock (Task 7.2).
  - `run_pre_production` builds/persists the record and the cliffhanger/compact
    clobber lives in the lines before `produce_runtime_config` (~760–815) (Tasks 6.1,
    6.2).
  - `_write_manifest` at ~389 → add `decisions` block (Task 13.1).
- `utils/story_planner.py` — `words_per_seg = config.get("script", {}).get(...)` at
  ~82 → use authoritative value passed in (Task 7.2).
- `utils/utils.py` — `validate_script` at ~179 → extend for word-count enforcement
  (Task 8.1).
- `agents/director_agent.py`:
  - `consult_with_writer` at ~1645 → feed Director's proposal in, capture rationale
    (Task 4.2).
  - `suggest_cliffhangers` at ~2050 (currently `return []`) → implement (Task 5.1).
  - `compact_story` at ~2058 (currently passthrough) → implement (Task 5.2).
- `memory/permanent_memory.py` — refactor into `ProjectStore`/`StoryStore` (Task 9.1),
  keep public API (`get_character`, `log_character`, `log_recurring_motif`,
  `check_continuity`).
- `utils/local_ui.py` — `run_pipeline_thread` → call unified pathway (Task 12.1);
  construct `DirectorAgent(config)` not `DirectorAgent(config.get("models", {}))`
  (Task 12.2).
- `bootstrap_pipeline.py` — `run_pipeline_with_args` argparse block → add
  `--run-mode` (Task 11.1).
- `config/config.yaml` + `config/config_schema.py` — add `script.word_count_tolerance`
  (0.25) and `script.word_count_max_retries` (2) (Task 8.2).

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"], "rationale": "DecisionRecord model underpins everything" },
    { "wave": 2, "tasks": ["2", "3", "9"], "rationale": "Versioning, blackboard, and memory refactor are independent and build on task 1" },
    { "wave": 3, "tasks": ["4", "10", "11"], "rationale": "DecisionEngine needs the model; visual lock and run-mode need the memory tiers" },
    { "wave": 4, "tasks": ["5", "15"], "rationale": "Length controls and risk-tiered intervention build on the DecisionEngine" },
    { "wave": 5, "tasks": ["6"], "rationale": "Wire pre-production to build/persist the record (needs engine, blackboard, length controls)" },
    { "wave": 6, "tasks": ["7", "13", "14"], "rationale": "Runtime obeys the record; manifest and backward-compat depend on wiring" },
    { "wave": 7, "tasks": ["8", "12"], "rationale": "Word-count enforcement follows runtime; dashboard unification needs wiring+memory+run-mode" },
    { "wave": 8, "tasks": ["16"], "rationale": "Full verification after all features land" }
  ]
}
```

Visual dependency overview:

```
1 (DecisionRecord) ─┬─► 2 (versioning)
                    ├─► 4 (DecisionEngine) ─┬─► 5 (length controls) ─► 6 (wire pre-production) ─► 7 (runtime obeys)
                    │                       └─► 15 (risk-tiered intervention)
                    └─► 3 (Blackboard) ─────► 6

6 ─► 7 ─► 8 (word-count enforcement)
6 ─► 13 (manifest provenance)
6 ─► 14 (backward-compat / resume)

9 (three-tier memory) ─► 10 (visual lock)
9 ─► 11 (run mode + flags)
6 + 9 + 11 ─► 12 (unify dashboard)

7 + 8 + 9 + 10 + 11 + 12 + 13 + 14 + 15 ─► 16 (full verification)
```

Critical path: 1 → 4 → 5 → 6 → 7 → 8 → 16.

## Notes

- After every task, run the relevant unit tests and `getDiagnostics` on changed files;
  keep the pipeline importable (`venv\Scripts\python.exe -c "import core.pipeline_long"`).
- Unit tests must mock Ollama and image-gen so no heavy GPU/model work runs in CI; some
  tests explicitly assert zero heavy calls (Performance, Req 15.6).
- Tasks 1–4 and 9 are additive (nothing changes behavior). Task 6 keeps behavior parity
  via `to_overlay()`. Task 7 is the first behavior change (runtime honors the record).
- Reuse existing modules rather than replacing: `config_schemas.py`,
  `CheckpointManager`, `WorldState`, `PermanentMemoryLog`, `WorkloadScheduler`.
- On-hardware manual checks (real `--file` run with a duration lock + cliffhanger) are
  documented in the design's Testing Strategy and are not part of automated CI.
