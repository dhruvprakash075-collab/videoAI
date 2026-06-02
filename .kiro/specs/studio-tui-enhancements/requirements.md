# Requirements Document

## Introduction

`studio_tui.py` is a Textual-based terminal control panel for the Video.AI pipeline.
It currently offers a single scrolling log, a one-line status bar, and a bottom
composer for entering a topic / answering Director pauses. It works, but it is
visually flat and surfaces very little of the rich state the pipeline already tracks
(segment progress, elapsed time, VRAM, the active question, the output path).

This spec enhances the TUI into a polished, multi-view operator console while keeping
it **safe to run on the operator's Windows machine** and **backward compatible** with
the existing FastAPI dashboard, which shares the same `UIState` object.

The work is purely operator-facing (presentation + interaction). It must not change
how the pipeline produces video, and it must not break the web dashboard
(`utils/local_ui.py`) that reads the same `UIState` fields.

## Scope and overlap policy

To avoid duplicating the web dashboard, features that the web UI already owns are
**deliberately out of scope** for the TUI:

- **Config editing** — owned by web `/api/config`. The TUI may *display* config
  read-only but does not edit it (prevents two UIs drifting the schema).
- **Voice management / preview** — owned by web `/api/upload_voice`,
  `/api/voices`, `/api/audio/preview`. A terminal cannot play audio inline anyway.
- **Benchmark / model-eval diagnostics** — owned by the CLI (`utils/benchmark.py`,
  `utils/model_eval.py`), which is the right place for long GPU jobs.

The TUI focuses on what a terminal does best: live monitoring, fast keyboard control,
and read-only inspection of what a run produced.

## Glossary

- **TUI**: the terminal UI defined in `studio_tui.py`, built on Textual 8.2.7.
- **UIState**: the shared class in `agents/director_agent.py` that both the TUI and the
  FastAPI dashboard read/write to communicate with the running pipeline thread.
- **Composer**: the bottom `Input` widget used to start a run or answer a pause.
- **Pause/question**: a point where the pipeline sets `UIState.status = "paused"` and
  `UIState.active_question`, waiting on `UIState.pause_event` for an operator reply
  (preview gate, Director mode, consultations).
- **Segment**: one fixed-length unit of the video; a run produces `n_segs` of them.
- **ETC**: estimated time to completion, derived from segments done vs total + elapsed.
- **Real terminal**: Windows Terminal / PowerShell / conhost — a TTY Textual can drive.
  The embedded Kiro/VS Code terminal is **not** a real terminal for Textual.
- **Run lifecycle states**: `idle → running ⇄ paused → complete | error`.

## Phasing overview

Delivery is split into three phases so value ships early and risk stays low. Each
requirement is tagged with its phase.

- **Phase 1 — Core console (R1–R9):** the visual + monitoring upgrade. Independently
  shippable; the biggest usability jump.
- **Phase 2 — Run control & convenience (R10–R15):** start/steer runs from the TUI,
  output access, quality-of-life. Mostly wiring over existing pipeline parameters.
- **Phase 3 — Operator inspection panels (R16–R18):** read-only/maintenance panels
  (preflight, checkpoints, artifacts). Nice-to-have; defer until P1–P2 are solid.

---

## Requirements

The requirements below are grouped by phase. Each is also tagged with its **Phase:**.

## Phase 1 — Core console

### Requirement 1: Multi-view layout (tabs)

**Phase:** 1

**User Story:** As an operator, I want the console organized into clear views, so I can
focus on logs while still being able to see run metrics and help on demand.

#### Acceptance Criteria

1. WHEN the TUI starts THEN it SHALL present a tabbed layout with at least three views:
   **Run** (logs + composer), **Stats** (live run metrics), and **Help** (keybindings).
2. The **Run** view SHALL remain the default focused tab on launch.
3. WHEN the operator switches tabs THEN the pipeline state and streaming logs SHALL be
   unaffected (switching is presentation-only).
4. WHERE the terminal is too small to show a tab's full content THEN the content SHALL
   scroll rather than crash or clip the layout.

### Requirement 2: Segment progress indication

**Phase:** 1

**User Story:** As an operator, I want to see how far along the run is, so I know
whether to wait or step away.

#### Acceptance Criteria

1. WHEN a run is in progress and the total segment count is known THEN the TUI SHALL
   display a progress bar showing completed-vs-total segments.
2. WHERE the total segment count is not yet known (pre-production) THEN the progress
   area SHALL show an indeterminate/“planning…” state instead of a misleading 0%.
3. WHEN a segment completes THEN the progress bar SHALL advance within one polling
   interval (≤ 0.5s perceived latency).
4. WHEN the run reaches `complete` THEN the progress bar SHALL show 100%.
5. IF the pipeline does not report progress (older code path) THEN the progress bar
   SHALL remain hidden or indeterminate and SHALL NOT display incorrect values.

### Requirement 3: Rich run-status panel (with ETC)

**Phase:** 1

**User Story:** As an operator, I want a clear at-a-glance status header, so I always
know the current phase, topic, elapsed time, and roughly how long is left.

#### Acceptance Criteria

1. WHEN the TUI is running THEN a status panel SHALL show: current status (with an
   icon/color), the topic, elapsed run time, and current/total segment when available.
2. WHEN `status == complete` THEN the panel SHALL show the final output video path.
3. WHEN `status == error` THEN the panel SHALL show an error indicator and keep the
   last error visible in the log.
4. The elapsed-time field SHALL use fixed-width (tabular) digits so it does not jitter
   as it updates.
5. WHERE segment progress is known THEN the panel SHALL show an estimated time to
   completion (ETC); IF it cannot be computed THEN it SHALL show `—`, not a fabricated
   value.
6. The status panel SHALL update at the existing polling cadence without flicker.

### Requirement 4: Live stats view (with VRAM gauge)

**Phase:** 1

**User Story:** As an operator on a 6GB GPU, I want a metrics view including memory use,
so I can monitor the run’s vitals and spot memory pressure without scrolling the log.

#### Acceptance Criteria

1. The **Stats** view SHALL display, at minimum: topic, status, segments completed/total,
   elapsed time, and the most recent log line.
2. WHERE VRAM information is available THEN the Stats view SHALL display current GPU
   VRAM usage (used/total + percent); otherwise it SHALL omit that row gracefully.
3. WHEN VRAM crosses a high-usage threshold THEN the gauge SHALL change appearance
   (icon/color) to flag memory pressure.
4. WHEN the underlying `UIState` changes THEN the Stats view SHALL refresh within one
   polling interval.
5. The Stats view SHALL degrade gracefully when fields are empty (show `—`, not blanks
   or exceptions).

### Requirement 5: Expanded, discoverable keybindings

**Phase:** 1

**User Story:** As an operator, I want useful keyboard shortcuts that are visible, so I
can control the run quickly.

#### Acceptance Criteria

1. The footer SHALL show the available keybindings.
2. The TUI SHALL support: quit, clear log, and switch between tabs via the keyboard.
3. WHERE a run is in progress THEN the TUI SHALL provide a key to request a pause and,
   when paused, the composer SHALL accept the reply (reusing the existing
   `UIState.pause_event` mechanism).
4. The TUI SHALL provide a key to jump the log to the latest entry (scroll to bottom).
5. IF a control is not valid in the current state (e.g. pause when idle) THEN pressing
   its key SHALL be a no-op with a brief notice, not an error.

### Requirement 6: Polished, coherent visual theme

**Phase:** 1

**User Story:** As an operator, I want the console to look clean and modern, so it is
pleasant and readable during long runs.

#### Acceptance Criteria

1. The TUI SHALL use one coherent dark palette across header, panels, log, and composer.
2. Log lines SHALL be visually differentiated by kind (agent/director, user, system,
   error) using valid Rich styles (hex colors), not CSS class names passed as styles.
3. Panels SHALL have titled, optically coherent borders (consistent radius/padding).
4. Status/error/success SHALL be communicated with both color and an icon/symbol (not
   color alone), to remain legible in low-color terminals.
5. Header and Footer SHALL NOT use a border (the current cause of the empty-box bug),
   and the theme SHALL NOT depend on features unavailable in Textual 8.2.7.

### Requirement 7: Backward compatibility and safety

**Phase:** 1

**User Story:** As the maintainer, I want the enhancements to not break the existing
pipeline or web dashboard, so nothing regresses.

#### Acceptance Criteria

1. Any new `UIState` fields/methods SHALL be additive and optional; existing readers
   (`utils/local_ui.py`, `agents/director_agent.py`, `core/pipeline_long.py`) SHALL
   continue to function unchanged.
2. WHEN the pipeline runs WITHOUT the TUI (CLI or web) THEN behavior SHALL be identical
   to today (no new mandatory dependencies, no new prompts).
3. The TUI SHALL run on Textual 8.2.7 as pinned; it SHALL NOT introduce APIs absent
   from that version.
4. WHEN any pipeline code reads a newly added progress field that was never set THEN it
   SHALL use a safe default and SHALL NOT raise.
5. New code SHALL follow the repo conventions: module logger over `print` in library
   code, `pathlib.Path`, and graceful handling of optional data.

### Requirement 8: Robust startup and failure messaging

**Phase:** 1

**User Story:** As an operator, I want clear guidance when the TUI can’t run or the
window is too small, so I’m not staring at a garbled screen.

#### Acceptance Criteria

1. WHEN Textual is not installed THEN the TUI SHALL print a clear install command and
   exit non-zero (existing behavior preserved).
2. WHEN launched in a terminal Textual cannot drive THEN the failure SHALL be captured
   to `studio_tui_crash.log` (existing crash hook preserved) and a human-readable hint
   SHALL be available to the operator.
3. WHERE a helper launcher exists (`launch_tui.ps1`) THEN it SHALL open the TUI in a
   real terminal window (Windows Terminal if present, else a new PowerShell window).
4. An uncaught exception during the run SHALL be logged to the crash log without
   silently discarding the traceback.
5. WHEN the terminal is smaller than a usable minimum (e.g. 80×24) THEN the TUI SHALL
   show a clear "resize to at least 80×24" message instead of a broken layout, and SHALL
   recover automatically when the terminal is enlarged.

### Requirement 9: Completion signal (bell)

**Phase:** 1

**User Story:** As an operator running long renders, I want an audible/visual signal
when a run finishes or fails, so I notice even if I’ve stepped away.

#### Acceptance Criteria

1. WHEN a run transitions to `complete` or `error` THEN the TUI SHALL emit a terminal
   bell (`self.bell()`) once.
2. The bell SHALL fire at most once per state transition (no repeated ringing while the
   status remains the same).
3. IF the terminal does not support a bell THEN the transition SHALL still be shown
   visually (toast + status panel) without error.

---

## Phase 2 — Run control & convenience

### Requirement 10: Run options without leaving the TUI

**Phase:** 2

**User Story:** As an operator, I want to set run options (duration, resume, RVC,
director/preview mode, project) from the TUI, so I don't have to drop to the CLI for
common variations.

#### Acceptance Criteria

1. The TUI SHALL provide a way to set, before starting a run: total duration (minutes),
   resume on/off, skip-RVC on/off, director-mode on/off, preview-mode on/off, and an
   optional project/series name.
2. WHEN the operator starts a run THEN the selected options SHALL be passed through to
   `run_long_pipeline(...)` using its existing parameters (no pipeline signature change).
3. WHERE an option is left unset THEN the pipeline default SHALL apply (identical to a
   bare `run_long_pipeline(topic=...)` call today).
4. The current option values SHALL be visible before launch (e.g. an options panel or
   a summary line), so the operator can confirm what will run.
5. IF an option value is invalid (e.g. non-numeric duration) THEN the TUI SHALL reject
   it with an inline notice and SHALL NOT start the run.

### Requirement 11: Start a run from a story file

**Phase:** 2

**User Story:** As an operator, I want to start a run from an existing story text file,
so I can produce videos from prepared scripts (the pipeline already supports `--file`).

#### Acceptance Criteria

1. The TUI SHALL accept a file path as run input and pass its contents via the existing
   `content_text` parameter of `run_long_pipeline`.
2. WHEN the path does not exist or is not readable THEN the TUI SHALL show a clear error
   and SHALL NOT start the run.
3. WHERE a file is used THEN the status panel SHALL indicate the source (file name)
   rather than a typed topic.
4. The file picker/entry SHALL accept Windows paths (`pathlib.Path`), including paths
   with spaces.

### Requirement 12: In-run controls (pause, cancel)

**Phase:** 2

**User Story:** As an operator, I want to pause or cancel a running pipeline from the
TUI, so I can intervene without killing the terminal.

#### Acceptance Criteria

1. WHILE a run is in progress THEN the TUI SHALL provide a manual pause that sets
   `UIState.status = "paused"` and an `active_question`, reusing the existing
   `pause_event` resume mechanism (mirrors the web `/api/manual_pause`).
2. WHEN paused THEN the composer SHALL accept a reply that resumes the run.
3. The TUI SHALL provide a cancel/abort affordance that requests a graceful stop and
   clearly reflects the resulting state; it SHALL NOT corrupt checkpoints (a cancelled
   run SHALL remain resumable per existing checkpoint behavior).
4. IF no run is active THEN pause/cancel SHALL be no-ops with a brief notice.

### Requirement 13: Output access and run history

**Phase:** 2

**User Story:** As an operator, I want quick access to the finished video and recent
runs, so I can review results without hunting through folders.

#### Acceptance Criteria

1. WHEN a run completes THEN the TUI SHALL show the absolute output path and SHALL
   provide a one-key action to open the output folder in the OS file manager.
2. The open action SHALL use a Windows-safe mechanism and SHALL degrade gracefully
   (notice, not crash) if it cannot open the folder.
3. WHERE prior runs exist under `studio_outputs/` THEN the TUI SHALL offer a list of
   recent outputs the operator can view/copy the path for.
4. The TUI SHALL NOT delete or modify any file under `studio_outputs/`.

### Requirement 14: Quality-of-life interactions

**Phase:** 2

**User Story:** As an operator, I want small conveniences (copy, command palette, toast
notifications, an optional session log, confirm-on-quit during a run), so the console
feels efficient and safe.

#### Acceptance Criteria

1. The TUI SHALL surface key actions through the Textual command palette (Ctrl+P) in
   addition to keybindings.
2. WHEN a notable event occurs (run started, paused, completed, error, copied) THEN the
   TUI SHALL show a transient toast/notification.
3. The TUI SHALL provide an action to copy the current output path (or last log line)
   to the clipboard where the terminal supports it; otherwise it SHALL show the value
   for manual copy without error.
4. WHEN the operator attempts to quit WHILE a run is active THEN the TUI SHALL require a
   confirmation, so an in-progress render is not lost by a stray keypress.
5. The TUI MAY (optional) write the visible session log to a plain-text file on demand;
   IF provided THEN it SHALL write under a run/log directory using `pathlib.Path` and
   SHALL report the path, and a write failure SHALL surface as a notice (no crash).
6. Each quality-of-life action SHALL be discoverable (palette entry, footer binding, or
   Help text).

### Requirement 15: Accessibility and resilience of new controls

**Phase:** 2

**User Story:** As an operator, I want the new controls to be keyboard-accessible and
robust, so the console stays usable and never traps me.

#### Acceptance Criteria

1. All new controls SHALL be operable by keyboard alone (focus order, Enter/Space
   activation); mouse is optional.
2. Any modal/dialog (options, confirm-quit, file entry) SHALL be dismissible with Escape
   and SHALL return focus to a sensible widget on close.
3. New controls SHALL communicate state with text/icon, not color alone (Req 6.4
   consistency), for low-color terminals.
4. A failure inside any new control handler SHALL be caught and surfaced (notice/log)
   without cancelling the poll interval or crashing the app.

---

## Phase 3 — Operator inspection panels

### Requirement 16: Preflight health panel

**Phase:** 3

**User Story:** As an operator, I want to see the system health checks at run start, so
I can fix a missing model or FFmpeg before wasting time.

#### Acceptance Criteria

1. The TUI SHALL surface the results of the existing preflight checks (Ollama endpoint,
   director/writer models present, FFmpeg on PATH, OmniVoice env, TTS engine, disk
   space) as a structured panel, not only as scrolling log lines.
2. Each check SHALL show an OK / WARN / FAILED state with an icon + color (not color
   alone) and a short detail string.
3. WHERE a check FAILED THEN the panel SHALL make it visually prominent.
4. The TUI SHALL provide an action to re-run preflight checks on demand without starting
   a full pipeline run.
5. IF preflight cannot run (import/connection error) THEN the panel SHALL show that
   gracefully without crashing the TUI.

### Requirement 17: Checkpoint / resume management

**Phase:** 3

**User Story:** As an operator, I want to see and manage resumable runs, so I can resume
or start fresh deliberately.

#### Acceptance Criteria

1. The TUI SHALL list existing checkpoints under `studio_checkpoints/` (topic, age,
   last completed step where available).
2. The TUI SHALL offer, per checkpoint: start a run that resumes it, or clear it to
   start fresh (using the existing `CheckpointManager.clear`).
3. WHEN clearing a checkpoint THEN the TUI SHALL require confirmation (destructive).
4. The TUI SHALL surface whether a checkpoint is stale (e.g. >48h old warning) using the
   same policy the manager already logs.
5. The TUI SHALL not corrupt or partially write checkpoint files.

### Requirement 18: Run artifacts viewer (manifest, segments, chapters, quality)

**Phase:** 3

**User Story:** As an operator, I want to inspect what a run produced, so I can verify
quality and settings without opening files by hand.

#### Acceptance Criteria

1. WHEN a run completes THEN the TUI SHALL be able to display the `run_manifest.json`
   summary (models, settings, segments completed, duration, quality result).
2. The TUI SHALL be able to show per-segment metadata from `segment_NN_meta.json`
   (mood, word count, SD params, OOM events) for a selected segment.
3. The TUI SHALL be able to display the generated `chapters.txt` (YouTube chapter
   markers).
4. The TUI SHALL display the quality-check result (`passed` + issues + key details such
   as size and duration) as a pass/fail panel.
5. WHERE an artifact is missing or malformed THEN the viewer SHALL show a clear "not
   available" message, never crash.
6. Artifact viewing SHALL be read-only (no modification of run outputs).

---

## Non-Goals

- No change to how video/audio/images are generated; presentation and interaction only.
- No remote/networked control surface; the TUI stays local to the operator’s machine.
- No replacement of the React/FastAPI dashboard; the two coexist on the same `UIState`.
- No config editing, voice management, or benchmark/model-eval in the TUI — those remain
  owned by the web dashboard and the CLI (see Scope and overlap policy).
- No mouse-first redesign; keyboard remains the primary interaction model.

## Verification Approach

- **Static**: `py_compile` on `studio_tui.py` and any edited pipeline modules; import
  smoke test; instantiate `StudioTUI()` headlessly.
- **Interactive**: drive the TUI in a real PTY (launch, snapshot, send a topic, switch
  tabs, trigger/answer a pause) to confirm layout renders and controls work.
- **Compatibility**: confirm `utils/local_ui.py` status endpoint still reads the same
  fields and a non-TUI run is unchanged.
- **Per phase**: each phase is independently verifiable and shippable; later phases must
  not regress earlier-phase behavior.
