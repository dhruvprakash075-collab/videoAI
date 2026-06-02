# Implementation Plan

## Overview

This plan turns the 18 requirements into incremental, test-as-you-go coding tasks for
`studio_tui.py` (plus tiny additive hooks in `agents/director_agent.py` and
`core/pipeline_long.py`). It follows the design's phase map. Each phase is independently
shippable; do not start a later phase until the earlier phase's verification gate passes.

**Builder notes**
- Run everything through the venv: `venv\Scripts\python.exe`.
- Verify TUI rendering in a **real terminal/PTY**, never the embedded editor terminal.
- Keep `UIState` changes additive; never change `run_long_pipeline`'s signature.
- After each task: `py_compile` the touched files and, where noted, drive the PTY.

## Task Dependency Graph

```
Phase 1 (Core console)
  1 (render fix + theme) ─┬─> 2 (tabs) ──────────────┬─> 5 (progress bar)
                          │                          ├─> 6 (status panel + ETC)
  3 (UIState fields) ─────┼─> 3.1 (unit tests)       ├─> 7 (Stats grid + VRAM)
                          └─> 4 (pipeline hooks) ────┘
  2,5,6,7 ─> 8 (poll/log-index hardening) ─> 9 (keybindings)
  1 ─> 10 (min-size + crash log)        3 ─> 11 (completion bell)
  [2..11] ─> 12 (Phase 1 gate)   ◀── must pass before Phase 2

Phase 2 (Run control)  [requires 12]
  13 (options form) ─> 14 (file source)
  15 (request_cancel hook) ─> 16 (pause/cancel UI)
  13,16 ─> 17 (output access) ─> 18 (QoL: palette/toast/copy/quit/log) ─> 19 (a11y)
  [13..19] ─> 20 (Phase 2 gate)  ◀── must pass before Phase 3

Phase 3 (Inspection)  [requires 20]
  21 (preflight collector, optional) ─> 22 (preflight panel)
  23 (checkpoints screen)   24 (artifacts viewer)
  [21..24] ─> 25 (Phase 3 gate + docs)
```

Critical path: 1 → 2 → 6/7 → 8 → 12 → 13 → 16 → 18 → 20 → 22/23/24 → 25.

Tasks within the same wave have no interdependencies and may be done in parallel. Each
wave depends on the previous wave completing.

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "3"], "parallel": true, "rationale": "Render/theme fix and additive UIState fields are independent foundations." },
    { "wave": 2, "tasks": ["2", "3.1", "4"], "parallel": true, "rationale": "Tabs (needs 1), UIState unit tests (needs 3), pipeline hooks (needs 3)." },
    { "wave": 3, "tasks": ["5", "6", "7", "10", "11"], "parallel": true, "rationale": "Progress bar, status panel, stats grid, min-size guard, bell — all build on tabs + fields." },
    { "wave": 4, "tasks": ["8", "9"], "parallel": false, "rationale": "Poll/log-index hardening then keybindings, after the widgets exist." },
    { "wave": 5, "tasks": ["12"], "parallel": false, "rationale": "Phase 1 verification gate — must pass before Phase 2." },
    { "wave": 6, "tasks": ["13", "15"], "parallel": true, "rationale": "Options form and the request_cancel pipeline hook are independent." },
    { "wave": 7, "tasks": ["14", "16"], "parallel": true, "rationale": "File source (needs 13) and pause/cancel UI (needs 15)." },
    { "wave": 8, "tasks": ["17"], "parallel": false, "rationale": "Output access builds on run-start + cancel paths." },
    { "wave": 9, "tasks": ["18", "19"], "parallel": false, "rationale": "QoL features then accessibility pass over all new controls." },
    { "wave": 10, "tasks": ["20"], "parallel": false, "rationale": "Phase 2 verification gate — must pass before Phase 3." },
    { "wave": 11, "tasks": ["21"], "parallel": false, "rationale": "Optional preflight collector refactor precedes the panel." },
    { "wave": 12, "tasks": ["22", "23", "24"], "parallel": true, "rationale": "Preflight panel, checkpoints screen, artifacts viewer are independent read-only screens." },
    { "wave": 13, "tasks": ["25"], "parallel": false, "rationale": "Phase 3 verification gate + docs." }
  ]
}
```

## Tasks

### Phase 1 — Core console (R1–R9)

- [ ] 0. Pre-flight: fix live bugs before the rewrite begins
  - **`local_ui.py` line 330:** replace `UIState.add_log(f"Received user response: '{reply}'...")` with `UIState.add_log(f'User response received ({len(reply or "")} chars). Resuming...')` — stops verbatim reply text leaking into the `/api/status` log feed.
  - **`UIState.pause_event` default:** change `pause_event = None` to `pause_event = threading.Event()` in `agents/director_agent.py` — eliminates `AttributeError` crash risk.
  - **`_director_abort` reset:** add `_director_set_abort(False)` as the first statement inside `run_long_pipeline()` in `core/pipeline_long.py` — prevents silent segment-skip on 2nd run after a cancel.
  - `py_compile` all three touched files.
  - _Requirements: 7.1, 7.2 (no regression)_

- [ ] 1. Fix the baseline render bug and theme foundation
  - Remove `border` from `Header` and `Footer` in the CSS (root cause of the empty-box bug).
  - Register a single `Theme` ("studio-dark") via `App.register_theme()` and set
    `self.theme`; centralize the GitHub-dark palette.
  - Keep the existing hex Rich styles for log lines (never CSS class names as styles).
  - Verify: launch in PTY, confirm Header title, Footer, and status area are non-empty.
  - _Requirements: 6.1, 6.2, 6.3, 6.5_

- [ ] 2. Build the tabbed layout skeleton (Run / Stats / Help)
  - Replace the single `Vertical` body with `TabbedContent` holding three `TabPane`s.
  - Run pane: status panel + progress bar + `RichLog` + composer `Input`.
  - Stats pane: empty `Grid` placeholder. Help pane: static keybindings text.
  - Default focus stays on the Run tab; tab switching is presentation-only.
  - Verify: PTY launch shows three tabs; switching does not disturb the log.
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [ ] 3. Add additive progress/metric fields + helpers to `UIState`
  - In `agents/director_agent.py`, add class attrs: `segment_current=0`,
    `segment_total=0`, `run_start_ts=0.0`, `vram_text=""`.
  - Add classmethods `reset_run(topic)` and `set_progress(current=None, total=None)`.
  - Keep all existing attrs/methods unchanged.
  - _Requirements: 7.1, 7.4_

- [ ] 3.1 Unit-test the new `UIState` helpers
  - Create `tests/conftest.py` with an `autouse=True` fixture that resets **all** UIState fields before each test (status, logs, topic, output_video, active_question, user_reply, pause_event, is_ui_mode, segment_current, segment_total, run_start_ts, vram_text).
  - Extract `_format_elapsed`, `_format_etc`, `_parse_duration` into `studio_tui_helpers.py` so they are testable without Textual.
  - Test `set_progress`: total-only, current-only, both-args, both-None, float-cast, negative/zero (define contract), non-castable string.
  - Test `reset_run`: zeroes counters, sets topic, `run_start_ts` within 1s of `time.time()`, clears vram_text, called twice from dirty state.
  - Test `_format_elapsed`: `run_start_ts=0` → `—`, elapsed 3600s → `01:00:00`.
  - Test `_format_etc`: `total=0` → `—`, `current=0` → `—`, `current==total` → `~0s`.
  - Test `_parse_duration`: `0` → None, `-5` → None, `'abc'` → None, `'  15  '` → 15, `99999` → None (upper bound 480).
  - Add `pytest-asyncio` to test deps for future Textual pilot tests.
  - Verify: `pytest tests/test_uistate.py -v` green; `py_compile` clean.
  - _Requirements: 7.1, 7.4_

- [ ] 4. Wire pipeline progress hooks (additive, thread-safe)
  - In `core/pipeline_long.py`, after outline reconciliation (final `n_segs`), call
    `UIState.set_progress(total=n_segs)`.
  - In the `finally` block of `process_segment`, inside the existing
    `completed_segs_lock`, call `UIState.set_progress(current=completed_segs)` — reuse
    the locked snapshot, never `UIState.segment_current + 1`.
  - Extend `_log_vram_usage()` to also set `UIState.vram_text` with the string it logs.
  - Verify: a `--dry-run` non-TUI run is unchanged; counters update.
  - _Requirements: 2.1, 2.3, 7.1, 7.2_

- [ ] 5. Implement the segment progress bar with planning + completion states
  - Use `ProgressBar(total=..., show_eta=True)`; on each poll
    `bar.update(total=segment_total, progress=segment_current)`.
  - `segment_total == 0` → indeterminate "planning…" via `update(total=None)`.
  - On `status == complete` → force `progress = total` (show 100%).
  - Verify: PTY shows planning → progress → 100%.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 6. Implement the status panel (badge + meta line) with ETC
  - Badge: `{icon} {STATUS} • {topic}` (● idle, ⠋ running, ⏸ paused, ✓ complete, ✗ error).
  - Meta line: `⏱ {elapsed} │ seg {cur}/{tot} │ VRAM {vram} │ {engines}`; show output
    path on complete; error indicator on error.
  - Elapsed uses fixed-width formatting; ETC derived from `cur/tot + run_start_ts`, or
    `—` when not computable.
  - Verify: PTY shows live elapsed + ETC; icons + color present.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 7. Build the Stats view (2×2 card grid + VRAM gauge)
  - `Grid` with `grid-size: 2 2`, fixed card `height`; cards: elapsed, segments,
    throughput (Sparkline wrapped in a titled `Vertical` card), engines.
  - Engines card also shows VRAM (used/total + %) when `vram_text` is set, plus the most
    recent log line; VRAM gauge changes icon/color past a high-usage threshold.
  - Missing values render `—`.
  - Verify: PTY Stats tab renders a balanced grid; no overflow.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [ ] 8. Harden the poll loop and log-index lifecycle
  - Wrap `_poll` body in try/except so a transient `query_one` miss never cancels the
    interval; surface failures to the log.
  - Reset `log_index = 0` when `len(UIState.logs) < log_index` (new run clears logs).
  - Fix `active_question` race: add `self._last_question: str = ""` to `__init__`; in
    `_poll` compare `UIState.active_question != self._last_question` instead of mutating
    UIState from the poll (the pipeline clears it after `pause_event.wait()` returns).
  - Add `self._throughput_samples: list[float] = []` to `__init__`; in `_poll`, when
    `segment_current` increases, append `1.0 / (elapsed / segment_current)` and cap at
    20 samples — this feeds the Stats sparkline with no UIState change.
  - Also reset `_director_set_abort(False)` in `on_input_submitted` before starting the
    pipeline thread (belt-and-suspenders alongside the Task 0 fix in `run_long_pipeline`).
  - Verify: starting a 2nd run in the same session resets progress/logs cleanly; pause
    prompt stays visible until the operator replies.
  - _Requirements: 3.6, 4.4, 5.5_

- [ ] 9. Expand keybindings + footer (Phase 1 set)
  - Add bindings: tab switch (`f1`/`f2`/`f3`), clear log (`ctrl+l`), scroll-to-latest
    (`ctrl+e`), quit (`ctrl+c`/`ctrl+q`); ensure the Footer lists them.
  - State-aware no-ops with a brief notice for invalid-state keys.
  - Verify: PTY footer shows bindings; each key works.
  - _Requirements: 5.1, 5.2, 5.4, 5.5_

- [ ] 10. Startup resilience: min-size guard + crash log
  - Preserve the existing `sys.excepthook` → `studio_tui_crash.log` and the
    `ImportError` install-hint exit.
  - Add an `on_resize`/size check: below ~80×24 show a centered "resize to at least
    80×24" notice and hide the main layout; restore when enlarged.
  - Verify: shrink the PTY → notice appears; enlarge → layout returns.
  - _Requirements: 8.1, 8.2, 8.4, 8.5_

- [ ] 11. Completion bell on terminal transitions
  - Track last-seen status; when it first becomes `complete`/`error`, call `self.bell()`
    exactly once; unsupported bell degrades to the toast/status visual.
  - Verify: drive status to complete → single bell; staying complete → no repeat.
  - _Requirements: 9.1, 9.2, 9.3_

- [ ] 12. Phase 1 verification gate (compatibility + PTY)
  - `py_compile` `studio_tui.py`, `agents/director_agent.py`, `core/pipeline_long.py`.
  - Confirm `utils/local_ui.py` status endpoint still returns
    `{status, active_question, logs, output_video}` unchanged (use FastAPI TestClient).
  - PTY end-to-end: tabs render, Header/Footer/status non-empty, progress + VRAM + ETC
    update, bell fires, min-size notice works.
  - PTY 2nd-run test: complete a run, start a 2nd run, assert early logs appear (log_index reset).
  - PTY pause-prompt test: trigger a pause, assert prompt stays visible until reply typed.
  - _Requirements: 7.1, 7.2, 7.3, 1.1, 2.1, 3.1, 4.1_

### Phase 2 — Run control & convenience (R10–R15)

- [ ] 13. Run options panel (collapsible form on the Run tab)
  - Add a `Collapsible("Run options")` with: duration `Input` (numeric), `Switch`es for
    resume / skip-RVC / director-mode / preview, project `Input`.
  - Build a kwargs dict of only the changed options; call
    `run_long_pipeline(topic=..., **opts)`; unset options omitted (defaults preserved).
  - Validate inputs: duration must be a positive integer ≤ 480 (upper bound prevents
    accidental multi-day runs); project name must pass `_safe_filename()` before use.
  - Invalid → error notice, run does not start.
  - Render a one-line summary of resolved options before launch.
  - Verify: unchanged panel → kwargs == `{topic}`; bad duration blocks start; duration
    of 99999 is rejected.
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [ ] 14. Start a run from a story file
  - Add a topic-vs-file source toggle; when file, read its contents into `content_text`
    using `pathlib.Path` (tolerate spaces/Windows paths).
  - **Security:** after `Path(path_str).resolve()`, assert the resolved path is within
    the project root (`candidate.is_relative_to(Path('.').resolve())`). Reject paths
    outside with a clear error notice.
  - Missing/unreadable file → clear error, no run start; status shows the file name.
  - Verify: real temp story file → content reaches `content_text`; status shows name;
    a path like `../../config/config.yaml` is rejected.
  - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [ ] 15. Expose a zero-coupling cancel hook in the pipeline
  - In `core/pipeline_long.py`, add `request_cancel()` that wraps
    `_director_set_abort(True)` (so the TUI never imports private module globals).
  - Verify: calling it sets `_director_aborted()` True; a subsequent `process_segment`
    skips; no checkpoint files are deleted.
  - _Requirements: 12.3_

- [ ] 16. In-run pause & cancel controls
  - Pause (`f5`): set `status="paused"` + a manual `active_question`, `pause_event.clear()`;
    composer reply sets `user_reply` + `pause_event.set()` to resume.
  - Cancel (`ctrl+x`, with confirm modal): call `request_cancel()`.
  - Pause/cancel when idle → no-op notice.
  - Verify: PTY pause→resume works; cancel keeps checkpoints; idle keys no-op.
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [ ] 17. Output access + run history
  - On complete, show absolute `output_video`; add `ctrl+o` to open its folder via
    `os.startfile(Path(output).parent)`, wrapped in try/except → notice on failure.
  - Add a read-only History list of recent `studio_outputs/` entries (by mtime) to
    view/copy a path; never write/delete there.
  - Verify: `os.startfile` (mocked) called with the folder; failure → toast only.
  - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [ ] 18. Quality-of-life: palette, toasts, copy, confirm-quit, session log
  - Register a command-palette `Provider` for Start/Pause/Cancel/Open/Clear/Switch/Quit.
  - Use `App.notify(...)` toasts for started/paused/completed/error/copied.
  - `ctrl+y` → `App.copy_to_clipboard(...)` (output path or last log) + confirmation toast.
  - `action_quit` while `_pipeline_active` → push a confirm `ModalScreen`.
  - Optional `ctrl+w` → write visible log to `logs/tui_session_{ts}.log` using
    `datetime.now().strftime('%Y%m%d_%H%M%S')` (NOT `isoformat()` — colons are illegal
    in Windows filenames); report path; write failure → notice.
  - Verify: confirm-quit modal blocks accidental exit; copy + session log work or notify;
    session log filename has no colons.
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

- [ ] 19. Accessibility + resilience of new controls
  - Ensure keyboard-only operation (focus order, Enter/Space); all modals are
    `ModalScreen` dismissible with Escape, restoring focus on close.
  - State via text+icon (not color alone); wrap every new handler so an exception becomes
    a notice/log entry, never a crash or a cancelled poll.
  - Verify: tab through controls with keyboard only; Escape closes every modal.
  - _Requirements: 15.1, 15.2, 15.3, 15.4_

- [ ] 20. Phase 2 verification gate
  - `py_compile` touched files; re-run Phase 1 PTY checks (no regression).
  - PTY: start-with-options, file source, pause/resume, cancel-keeps-checkpoints, open
    output, confirm-quit, copy, optional session log.
  - PTY: **pause→cancel sequence** — pause a run, then press Ctrl+X; assert confirm
    modal appears, confirm, assert no deadlock and status reflects cancelled.
  - PTY: **min-size recovery** — resize PTY to 40×10, assert notice; resize back to
    100×30, assert layout restored.
  - Security: verify `../../config/config.yaml` as file input is rejected; verify
    duration 99999 is rejected; verify session log filename has no colons.
  - _Requirements: 10.2, 11.1, 12.1, 13.1, 14.4, 15.1_

### Phase 3 — Operator inspection panels (R16–R18)

- [ ] 21. (Optional, preferred) Extract a pure preflight collector
  - Refactor the check-building inside `run_preflight_checks` into
    `collect_preflight_checks(config) -> dict` (no logging side effects); have the
    existing logger call it so behavior is unchanged.
  - Verify: existing preflight log output is identical; the dict has per-check
    name/status/detail.
  - _Requirements: 16.1_

- [ ] 22. Preflight health panel
  - Add a panel/screen rendering the checks as OK/WARN/FAILED rows with icon+color;
    FAILED rows visually prominent.
  - Add `f6` to re-run checks on a background thread (no full pipeline run).
  - Import/connection failure → graceful "unavailable", no crash.
  - Verify: PTY shows the panel; re-run works; simulated failure degrades gracefully.
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

- [ ] 23. Checkpoint / resume management screen
  - List `studio_checkpoints/*.json` (topic from filename, age from mtime, last step
    from JSON keys); flag stale (>48h) per the manager's policy.
  - **Security:** pass every topic stem through `_safe_checkpoint_topic()` before
    displaying or passing to `CheckpointManager.clear()`. Reject any stem that resolves
    outside `studio_checkpoints/`.
  - Per entry: Resume (start run with `resume=True`) or Clear (confirm modal →
    `CheckpointManager.clear(topic)`).
  - Verify: list renders; clear requires confirmation; a crafted `../config` stem is
    rejected; no partial writes.
  - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_

- [ ] 24. Run artifacts viewer (read-only)
  - Read `studio_outputs/{topic}/run_manifest.json` → summary panel (models, settings,
    segments, duration, quality pass/fail + issues).
  - Read `segments/segment_NN_meta.json` for a selected segment; read `chapters.txt`.
  - Missing/malformed → "not available" (try/except + JSON guard); never write.
  - Verify: valid run renders; deleting/corrupting a JSON → "not available", no crash.
  - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

- [ ] 25. Phase 3 verification gate + docs
  - `py_compile` touched files; re-run Phase 1+2 PTY checks (no regression).
  - PTY: preflight panel + re-run, checkpoint list + confirmed clear, artifact viewer
    handles missing files.
  - Update the Help pane and `launch_tui.ps1` reference so all screens are discoverable.
  - _Requirements: 16.1, 17.1, 18.1, 8.3_

## Notes

- **Builder guide:** see `builder-guide.md` in this spec folder for concrete code
  patterns derived from Python, error-handling, security, accessibility, UI-polish, and
  TDD global skills. Read it before writing any code.
- **Scope guardrail:** no config editing, voice management, or benchmark/model-eval in
  the TUI — those stay owned by the web dashboard and CLI (see the requirements' overlap
  policy). Do not add tasks for them.
- **Backward compatibility is non-negotiable:** every `UIState` change is additive with a
  safe default; the FastAPI dashboard and a non-TUI CLI run must behave exactly as today.

### Critical bugs to fix before/during Phase 1

- **`_director_abort` is never reset between runs (CRITICAL):** `_director_abort` is a
  module-level global in `pipeline_long.py` initialized to `False` at import time. After
  a cancel, it stays `True`. Every subsequent run silently skips all segments. Fix:
  call `_director_set_abort(False)` at the top of `run_long_pipeline()` AND in
  `on_input_submitted()` before starting the pipeline thread. One line each.
- **`log_index` never resets on 2nd run (HIGH):** `UIState.logs = []` is set on new run
  but `log_index` keeps incrementing. All early logs of the 2nd run are silently dropped
  until the new run produces more entries than the entire previous run. Fix: add
  `if len(UIState.logs) < self.log_index: self.log_index = 0` at the top of the
  log-draining block in `_poll()`. Already in the design spec — must be in the code.
- **`UIState.active_question` race (MEDIUM):** `_poll()` sets
  `UIState.active_question = None` to prevent repeated display, but the pipeline thread
  may still be blocked on `pause_event.wait()` reading it. The prompt can vanish and the
  user cannot resume. Fix: track `self._last_question` in the TUI instance; compare
  instead of mutating UIState from the poll.
- **`UIState.pause_event = None` crash risk (MEDIUM):** Any code path that sets
  `is_ui_mode=True` before a topic is submitted will crash with `AttributeError` when
  the pipeline calls `pause_event.clear()`. Fix: change the class default to
  `pause_event = threading.Event()`.

### Security fixes required per phase

- **Fix now (live):** `utils/local_ui.py` line 330 — stop logging `user_reply` verbatim.
  Replace with `UIState.add_log(f'User response received ({len(reply or "")} chars).')`.
- **Phase 2 gate:** Story file input must use `Path.resolve()` + containment check (not
  bare `Path.read_text()`). Duration must have an upper bound (e.g. 480 min). Session
  log filename must use `strftime('%Y%m%d_%H%M%S')` not `isoformat()` (colons are
  illegal in Windows filenames).
- **Phase 3 gate:** Checkpoint topic from `iterdir()` must go through
  `_safe_checkpoint_topic()` before `CheckpointManager.clear()`.

### Test strategy fixes required

- **UIState reset fixture (CRITICAL):** UIState uses class-level (global) state. Tests
  bleed between runs. Add a `conftest.py` fixture with `autouse=True` that resets ALL
  UIState fields before each test — not just the 4 new ones.
- **Textual pilot API (HIGH):** 80%+ coverage is impossible without `textual.testing`.
  Textual 8.2.7 ships `textual.testing.Pilot` (`async with app.run_test() as pilot`).
  Add `pytest-asyncio` to test deps and use the pilot for widget-level tests.
- **Extract pure helpers (HIGH):** Move `_format_elapsed`, `_format_etc`,
  `_parse_duration`, `_safe_checkpoint_topic` into a standalone `studio_tui_helpers.py`
  so they are importable without instantiating Textual.

### Architecture notes

- **`_director_abort` reset:** add to `run_long_pipeline()` start AND `on_input_submitted`.
- **Throughput sparkline data source:** maintain `self._throughput_samples: list[float]`
  in the TUI; append `1.0 / (elapsed / segment_current)` each time `segment_current`
  increases in `_poll`. Cap at 20 samples. No UIState change needed.
- **UIState home (tech debt):** UIState lives in `director_agent.py` (wrong semantic
  home). Moving it to `utils/shared_state.py` eliminates the latent circular import risk
  and reduces import cost for the cancel wrapper. Schedule post-Phase 1.
- **Concurrency gotcha (Task 4):** progress must be published from the locked
  `completed_segs` snapshot, never `UIState.segment_current + 1` — the racy version was
  empirically shown to lose updates under parallel segments (159/200 trials wrong).
- **Verified APIs (Textual 8.2.7 / Rich 14.3.4):** `TabbedContent`, `TabPane`,
  `ProgressBar.update(total=None)` (indeterminate), `Sparkline` (+ `sparkline--min/max-color`),
  `Grid`, `App.register_theme`, `App.notify`, `App.copy_to_clipboard`, `App.bell`,
  `ModalScreen`, command `Provider`; plus `os.startfile` and
  `core.pipeline_long._director_set_abort` / `_director_aborted`,
  `CheckpointManager.clear`.
- **Testing tools:** unit tests with the repo's test runner; interactive checks via a
  real PTY (launch `studio_tui.py`, snapshot, send keys). Clean up any throwaway probe
  scripts.
- **Each phase ends with a verification gate task** (12, 20, 25). A phase is "done" only
  when its gate passes and earlier phases show no regression.
