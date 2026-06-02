# Design Document

## Overview

This design upgrades `studio_tui.py` from a single-pane log/composer into a polished,
multi-view Textual console, while keeping the pipeline and the FastAPI dashboard
untouched. It is grounded in the **actually installed** stack, an **observed baseline**,
and a **rendered, interactively-tested prototype** — not assumptions:

- Verified versions: **Textual 8.2.7**, **Rich 14.3.4**, Python 3.12 (venv).
- Verified widget availability (imported successfully against the venv):
  `TabbedContent`, `TabPane`, `ProgressBar`, `DataTable`, `Digits`, `Rule`, `Sparkline`,
  `Header`, `Footer`, `Input`, `RichLog`, `Static`, `Label`, the `Grid` container, and
  the full `textual.theme.Theme(...)` constructor.
- Observed baseline (driven in a real PTY via the tui harness): the current
  `Header`, status `Static`, and `Footer` render as **empty boxes**. Root cause: the
  CSS applies `border: round` to `Header` and `Footer`, which are single-row chrome
  widgets — the border eats the row and clips their content. The redesign removes
  borders from chrome widgets and uses a dedicated status panel instead.
- **Prototype validation (real PTY):** a throwaway `_tui_proto.py` mock of the proposed
  layout was launched in a true terminal and tab-switched via keys. Confirmed working:
  tabbed Run/Stats/Help, hero banner, spinner status badge, meta line
  (`seg`, VRAM, engines), a real `ProgressBar` at 25%, a titled color-coded `RichLog`,
  and a 2×2 Stats grid (elapsed · segments · throughput sparkline · engines). Two layout
  bugs were caught and fixed during prototyping and are baked into this design:
  1. A bare `Sparkline` placed directly in a `Grid` overflowed and overlapped the
     neighboring card → **fix:** wrap the `Sparkline` in a titled `Vertical` card and
     give every grid cell a fixed `height`.
  2. `Grid(grid-size: 2)` with `height: auto` cards produced uneven cells → **fix:**
     use `grid-size: 2 2` with explicit `grid-gutter` and fixed card `height: 7`.

### Visual concept (validated)

```
 ⭘  Studio Console                                            22:49
 Run   Stats   Help
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ◤ VIDEO.AI  STUDIO CONSOLE
   ⠋ RUNNING  •  The Last Lighthouse Keeper
  ⏱ 04:127  │ seg 3/12  │ VRAM 4.8/6.0 GB  │ edge-tts · SD1.5
  ━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━  25%
 ╭─ live log ───────────────────────────────────────────────────╮
 │ ● Director analyzing 'The Last Lighthouse Keeper'…            │
 │ ▸ operator: accept                                           │
 │ ✗ seg_04 OOM — retrying at 512px                             │
 ╰──────────────────────────────────────────────────────────────╯
 ╭──────────────────────────────────────────────────────────────╮
 │  › Enter a topic, or reply to a Director pause…              │
 ╰──────────────────────────────────────────────────────────────╯
 ^q Quit  f1 Help  f2 Run  f3 Stats  ^l Clear          ^p palette

 Stats tab → 2×2 grid:  [elapsed] [segments] / [throughput ▁▄▆█] [engines]
```

### Goals
- Tabbed layout (Run / Stats / Help), segment progress bar, rich status panel, live
  stats, expanded + discoverable keybindings, coherent theme.
- Strictly additive `UIState` changes; web dashboard and CLI behavior unchanged.

### Non-Goals
- No change to media generation; no networked control; no mouse-first redesign.
- No config editing, voice management, or benchmark/model-eval in the TUI (owned by the
  web dashboard / CLI — see the requirements' overlap policy).

### Phase map & build order (for the builder)

This design is structured so each phase is independently shippable. Build in order; do
not start a later phase until the earlier one is verified.

| Phase | Requirements | Files touched | Verification gate |
| --- | --- | --- | --- |
| **P1 Core console** | R1–R9 | `studio_tui.py` (rewrite layout), `agents/director_agent.py` (UIState additive fields), `core/pipeline_long.py` (3 progress hooks) | PTY: tabs render, Header/Footer/status non-empty, progress + VRAM + ETC update; `--dry-run` non-TUI unchanged |
| **P2 Run control** | R10–R15 | `studio_tui.py` (options form, modals, palette), `core/pipeline_long.py` (`request_cancel()` wrapper) | PTY: start with options, pause/resume, cancel keeps checkpoints, open output, confirm-quit |
| **P3 Inspection** | R16–R18 | `studio_tui.py` (screens), optional pure `collect_preflight_checks()` refactor | PTY: preflight panel, checkpoint list + confirmed clear, artifact viewer handles missing files |

**Smallest viable first step:** the baseline render bug (R6.5 — remove `border` from
Header/Footer) plus the tab skeleton (R1). That alone makes the TUI usable and is the
foundation everything else attaches to.



## Architecture

### Component diagram

```
StudioTUI(App)                         agents/director_agent.UIState  (shared state)
├─ Header (clock, title)        ◀──poll──  status / topic / output_video
├─ TabbedContent
│   ├─ TabPane "Run"                       logs[]            (streaming)
│   │   ├─ StatusPanel (Static)            active_question   (pause prompt)
│   │   ├─ ProgressBar  #seg_progress      pause_event       (resume signal)
│   │   ├─ RichLog      #log_container      user_reply        (reply channel)
│   │   └─ Input        #composer
│   ├─ TabPane "Stats"
│   │   └─ Grid of Static cards   ◀──────  + segment_current / segment_total (new)
│   │      (elapsed·segments·            + run_start_ts                    (new)
│   │       throughput·engines)          + vram_text                       (new)
│   └─ TabPane "Help"
│       └─ Static (keybindings)
└─ Footer (bindings)
        │
        └── set_interval(0.4, _poll)  ── single reader of UIState ──▶ updates widgets
```

The TUI remains a **pure reader** of `UIState` (plus the existing write of
`user_reply`/`status`/`pause_event` to start runs and answer pauses). All pipeline
work still happens in the background thread started by `on_input_submitted`.

### Threading & data flow (unchanged contract)
- Pipeline runs in a daemon thread (existing).
- The pipeline thread only ever **writes** `UIState`; the TUI only **reads** it on the
  0.4s interval and writes back `user_reply` + sets `pause_event` (existing pattern).
- New progress fields are written by the pipeline thread and read by the poller — same
  single-writer/single-reader discipline already in use for `logs`/`status`. The
  existing `UIState._log_lock` covers `logs`; scalar fields are independent ints/strs
  where a torn read is harmless (worst case: one stale frame, corrected next tick).

## UIState additions (additive, backward-compatible)

Added to `agents/director_agent.UIState` as class attributes with safe defaults so any
reader works even if they are never set (Req 7.4):

```python
# Progress / metrics (additive — optional, safe defaults)
segment_current = 0       # int: segments completed so far
segment_total   = 0       # int: total planned segments (0 = unknown / planning)
run_start_ts    = 0.0     # float: time.time() when the run began (0 = not started)
vram_text       = ""      # str: human-readable VRAM line, or "" if unavailable

@classmethod
def reset_run(cls, topic: str):
    """Initialize per-run metrics (called by any UI before starting a run)."""
    cls.topic = topic
    cls.segment_current = 0
    cls.segment_total = 0
    cls.run_start_ts = time.time()
    cls.vram_text = ""

@classmethod
def set_progress(cls, current: int = None, total: int = None):
    """Update segment progress. Either arg optional; ignores None."""
    if total is not None:
        cls.segment_total = int(total)
    if current is not None:
        cls.segment_current = int(current)
```

Rationale: `utils/local_ui.py` already assigns an undeclared `UIState.run_thread` at
runtime, proving ad-hoc additive attributes are safe here. Declaring them on the class
(with defaults) is strictly safer than that existing pattern.

### Pipeline reporting hooks (minimal, in `core/pipeline_long.py`)

Three tiny, guarded writes — each wrapped so a non-UI run is unaffected:

1. **Total known** — set the denominator **after outline reconciliation** (after the
   final `n_segs = len(outline)` adjustments near lines 1428–1434):
   `UIState.set_progress(total=n_segs)`
2. **Segment done** — inside the existing `completed_segs_lock` in the `finally` block
   of `process_segment`, reuse the already-locked snapshot
   (`completed_segs = completed_segs_counter`): `UIState.set_progress(current=completed_segs)`.
   **Concurrency-critical:** do NOT use `UIState.segment_current + 1` — with parallel
   segment threads that is a racy read-modify-write.
3. **VRAM** — extend the existing `_log_vram_usage()` to also set
   `UIState.vram_text` with the same string it logs (it already computes used/total).

All three only publish numbers the TUI reads — they never change pipeline behavior.
The `is_ui_mode` guard is **optional** (a micro-optimization for CLI runs), not required
for correctness: `UIState` is always importable, and writing class attrs that nobody
reads in CLI mode is harmless and cannot raise. `run_start_ts`/`reset_run` is called by
the TUI (and optionally `local_ui.py`) when a run starts — the pipeline does not depend
on it.

## Components and Interfaces

### `StudioTUI(App)` — `studio_tui.py`
- `compose()` → yields `Header`, `TabbedContent` with three `TabPane`s (Run/Stats/Help),
  `Footer`. Run pane yields `StatusPanel(Static)`, `ProgressBar`, `RichLog`, `Input`.
- `on_mount()` → register theme, set `self.theme`, start `set_interval(0.4, self._poll)`.
- `_poll()` → single reader of `UIState`; updates the status panel, progress bar, and
  Stats card grid, drains new `logs`, and handles the paused/active_question prompt.
  Wrapped in try/except so one bad frame never kills the interval. **Resets
  `log_index = 0` when `len(UIState.logs) < log_index`** (a new run replaces `logs`
  with `[]`).
- `on_input_submitted(event)` → if paused, write `UIState.user_reply` + set
  `pause_event`; else call `UIState.reset_run(topic)` (zeroes segment counters + sets
  `run_start_ts`, so a 2nd run in the same session shows planning, not stale `12/12`)
  and start the pipeline thread.
- Actions: `action_quit`, `action_clear_log`, `action_show_help`, `action_show_run`,
  `action_show_stats`, `action_scroll_end`, `action_request_pause`.

### `UIState` — `agents/director_agent.py` (additive only)
- New class attrs: `segment_current:int=0`, `segment_total:int=0`,
  `run_start_ts:float=0.0`, `vram_text:str=""`.
- New classmethods: `reset_run(topic)`, `set_progress(current=None, total=None)`.
- Existing attrs/methods (`status`, `logs`, `add_log`, `pause_event`, `user_reply`,
  `active_question`, `output_video`, `topic`, `is_ui_mode`) are unchanged.

### `core/pipeline_long.py` — reporting hooks (additive)
- After `n_segs` finalized (post outline reconciliation): `UIState.set_progress(total=n_segs)`.
- In the `finally` block of `process_segment`, inside the existing `completed_segs_lock`:
  `UIState.set_progress(current=completed_segs)` (reuse the locked snapshot — never `+1`).
- `_log_vram_usage(label)` also sets `UIState.vram_text` with the string it already
  computes.

### `utils/local_ui.py` — unchanged
- The `get_system_status()` endpoint keeps returning exactly
  `{status, active_question, logs, output_video}`. New fields are not required by it.

## Data Models

### Run metrics (in-memory, on `UIState`)
| Field | Type | Default | Meaning | Written by | Read by |
| --- | --- | --- | --- | --- | --- |
| `status` | str | `"running"` | lifecycle state | pipeline/TUI | TUI, web |
| `topic` | str | `""` | current topic | TUI/web | TUI, web |
| `logs` | list[str] | `[]` | streaming log (lock-guarded) | pipeline | TUI, web |
| `output_video` | str | `""` | final path | pipeline | TUI, web |
| `active_question` | str/None | `None` | pause prompt | pipeline | TUI, web |
| `segment_current` | int | `0` | segments done | pipeline | TUI |
| `segment_total` | int | `0` | total segments (0=unknown) | pipeline | TUI |
| `run_start_ts` | float | `0.0` | run start epoch | TUI | TUI |
| `vram_text` | str | `""` | VRAM line or "" | pipeline | TUI |

### Derived (computed in `_poll`, not stored)
- `elapsed = time.time() - run_start_ts` when `run_start_ts > 0`, formatted `mm:ss`/`h m s`.
- `progress_fraction = segment_current / segment_total` when `segment_total > 0`, else
  indeterminate.

## Correctness Properties

### Property 1: Additive safety
With none of the new fields ever set, every existing reader (`local_ui`,
`director_agent`, `pipeline_long`, CLI) behaves exactly as today.

**Validates: Requirements 7.1, 7.2, 7.4**

### Property 2: Monotonic progress
`0 ≤ segment_current ≤ segment_total` whenever `segment_total > 0`; the bar never shows
>100% and never regresses during a run.

**Validates: Requirements 2.1, 2.3, 2.4**

### Property 3: Planning state
`segment_total == 0` ⇒ the UI shows planning/indeterminate, never a misleading 0%/100%.

**Validates: Requirements 2.2, 2.5**

### Property 4: Single-writer discipline
Each new scalar is written only by the pipeline thread (or TUI for `run_start_ts`) and
read only by the poller; a torn read self-corrects on the next 0.4s tick.

**Validates: Requirements 7.1, 7.4**

### Property 5: Presentation isolation
Switching tabs or clearing the log never mutates pipeline state or `logs` ordering;
`log_index` tracking stays consistent.

**Validates: Requirements 1.3, 5.5**

### Property 6: Resilient poll
An exception inside `_poll` is caught and surfaced to the log without cancelling the
interval timer.

**Validates: Requirements 3.5, 4.3, 8.4**

### Property 7: Cancel preserves resumability
Requesting cancel sets the pipeline abort flag so remaining segments skip; no checkpoint
is deleted, so the run remains resumable on a later launch. Cancel/pause when idle is a
no-op.

**Validates: Requirements 12.3, 12.4**

### Property 8: Options are pass-through, defaults preserved
The run-options panel only forwards options the operator changed; an unchanged panel
produces a call equivalent to `run_long_pipeline(topic=...)` today. Invalid options
block the run instead of starting with bad values.

**Validates: Requirements 10.2, 10.3, 10.5, 11.2**

### Property 9: Read-only inspection
Output history (R13), checkpoint listing (R17), and artifact viewing (R18) only read
disk. The only mutation in these panels is **clear checkpoint**, which is explicit and
confirmation-gated. No browse/inspect action creates, modifies, or deletes run outputs.

**Validates: Requirements 13.4, 17.3, 18.6**

### Property 10: Bell fires once per transition
The completion bell rings exactly once when status enters `complete`/`error` and does
not repeat while the status is unchanged; an unsupported bell degrades to a visual
notice without error.

**Validates: Requirements 9.1, 9.2, 9.3**

### Property 11: Preflight/diagnostics isolation
Re-running preflight executes on a background thread and surfaces import/connection
failures as a notice without crashing the app or starting a full pipeline run.

**Validates: Requirements 16.4, 16.5**

## Operator inspection panels (Phase 3 — Requirements 16–18)

These surface pipeline state that today lives only in logs or on-disk JSON. Design
principle: **prefer reading existing on-disk artifacts** over adding `UIState` fields,
so the TUI stays a thin reader and nothing in the pipeline must change. Out of scope by
the overlap policy: config editing, voice management, and benchmark/model-eval (owned by
the web dashboard and CLI).

### Preflight health panel (Req 16)
`run_preflight_checks` currently only logs a table. Two options, lowest-coupling first:
- **Preferred:** refactor the check-building in `run_preflight_checks` into a pure
  `collect_preflight_checks(config) -> dict` (no logging side-effects) that both the
  existing logger and the TUI call. The TUI renders the dict in a `DataTable`/grid with
  OK/WARN/FAILED icons. A "re-run checks" action (`f6`) calls it on a background thread.
- **Fallback (zero pipeline change):** the TUI calls the existing function and parses the
  structured result if exposed. Either way, FAILED rows are highlighted.

### Checkpoint / resume management (Req 17) — Checkpoints screen
Lists `studio_checkpoints/*.json` (topic from filename, age from mtime, completed steps
from the JSON keys). Per entry: **Resume** (start a run with `resume=True`) or **Clear**
(confirm modal → `CheckpointManager.clear(topic)`). Stale (>48h) flagged using the
manager's existing policy. Read-only listing; clearing is the only mutation and is gated
by confirmation.

### Run artifacts viewer (Req 18) — Artifacts screen
Reads on-disk JSON the pipeline already writes under `studio_outputs/{topic}/`:
- `run_manifest.json` → summary panel (models, settings, segments, duration, quality).
- `segments/segment_NN_meta.json` → per-segment detail (mood, words, SD params, OOM).
- `chapters.txt` → plain-text chapters view.
- quality result is embedded in the manifest; render `passed` + issues as a pass/fail
  panel. Missing/malformed files → "not available" message (try/except + JSON guard).
All strictly read-only.

### VRAM gauge & ETC (folded into Phase 1, Req 3 & 4)
Not a separate panel — these live in the core console:
- **VRAM gauge** (Req 4.2–4.3) — fed by `UIState.vram_text` which mirrors
  `_log_vram_usage`; color/icon shifts past a high-usage threshold.
- **ETC** (Req 3.5) — **derived in the TUI** from `segment_current/segment_total` +
  `run_start_ts` (no pipeline change). Fallback if ever needed: publish `eta_text` as an
  additive field set inside the existing `completed_segs_lock`.

### Screens summary
Core tabs: **Run · Stats · Help** (Phase 1). Phase 3 adds **Checkpoints**, **Artifacts**,
and a **Preflight** panel — reachable via the command palette and a screen menu so the
Run tab stays uncluttered. Each screen is keyboard-navigable and Escape-dismissible
(Req 15).

## Run control & convenience (Phase 2 — Requirements 10–15)

These extend the console with run control and convenience features that reuse existing
pipeline parameters and mechanisms — no pipeline signature changes.

### Run options panel (Req 10, 11) — collapsible form on the Run tab
A `Collapsible("Run options")` above the composer containing keyboard-accessible inputs:
- duration `Input` (numeric) → `duration_min`
- `Switch`/`Checkbox`: resume (→ `resume`), skip-RVC (→ `skip_rvc`),
  director-mode (→ `director_mode`), preview (→ `preview_mode`)
- project `Input` (optional) → `project_name` (+ `series_mode` when set)
- source toggle: typed **topic** vs **file path** (→ reads file into `content_text`)

On launch, the TUI builds a kwargs dict containing only the options the operator changed
and calls `run_long_pipeline(topic=..., **opts)`. Unset options are omitted so pipeline
defaults apply unchanged (Req 10.3). Invalid values (non-numeric duration, missing file)
are rejected with `self.notify(..., severity="error")` and the run does not start
(Req 10.5, 11.2). A one-line **summary** of resolved options renders before launch
(Req 10.4). File input uses `pathlib.Path`, tolerating spaces and Windows paths (Req 11.4).

`run_long_pipeline` already accepts: `duration_min`, `resume`, `skip_rvc`,
`director_mode`, `preview_mode`, `series_mode`, `project_name`, `content_text`,
`words_per_segment`, `images_per_segment`, `segment_count` — so this is pure wiring.

### In-run controls: pause & cancel (Req 12)
- **Pause** (`f5`): set `UIState.status="paused"` + a manual `active_question` and
  `pause_event.clear()`, mirroring the web `/api/manual_pause`. The composer reply sets
  `user_reply` + `pause_event.set()` to resume (existing path).
- **Cancel** (`ctrl+x`, with confirm): the pipeline already has a thread-safe abort flag
  (`_director_set_abort(True)` / `_director_aborted()`), checked at the top of every
  `process_segment` (line ~1515). The TUI cancel calls a tiny **new additive helper**
  exposed for this purpose so the TUI doesn't import private module globals — preferred,
  zero-coupling: expose `core.pipeline_long.request_cancel()` that wraps
  `_director_set_abort(True)`. Remaining segments skip; **checkpoints are preserved**
  (segments are skipped, not deleted), so the run stays resumable (Req 12.3). Cancel when
  idle → no-op notice (Req 12.4).

### Output access & history (Req 13)
- On `complete`, the status panel shows the absolute `output_video` path and a footer
  key (`ctrl+o`) opens its folder via `os.startfile(Path(output).parent)` (Windows-safe),
  wrapped in try/except → notice on failure (Req 13.2).
- A **History** affordance lists recent entries under `studio_outputs/` (read-only via
  `Path("studio_outputs").iterdir()` sorted by mtime); selecting one copies/shows its
  path. The TUI never writes/deletes under `studio_outputs/` (Req 13.4).

### Quality-of-life (Req 14)
- **Command palette**: register a small `Provider` exposing Start/Pause/Cancel/Open
  output/Clear log/Switch tab/Quit, so everything is reachable via Ctrl+P (Req 14.1).
- **Toasts**: use `App.notify(...)` for run started/paused/completed/error/copied
  (Req 14.2).
- **Copy**: `ctrl+y` calls Textual's built-in `App.copy_to_clipboard(value)` (OSC 52)
  with the output path or last log line; always shows a confirmation toast so it works
  even when the terminal ignores OSC 52 (Req 14.3).
- **Confirm-on-quit while running**: `action_quit` checks `_pipeline_active`; if active,
  push a `ModalScreen` confirm dialog instead of exiting immediately (Req 14.4).
- **Optional session log** (`ctrl+w`): write the visible log to
  `logs/tui_session_{timestamp}.log` via `pathlib.Path`; report the path in a toast;
  a write failure surfaces as a notice, never a crash (Req 14.5).

### Accessibility & resilience of new controls (Req 15)
- All inputs/switches/buttons are in the natural focus order; Enter/Space activate;
  modals are `ModalScreen` subclasses dismissible with Escape, restoring focus on close.
- State shown with text+icon, not color alone (consistent with Req 6.4).
- Every new handler is wrapped so an exception becomes a toast/log entry and never
  cancels the `_poll` interval or crashes the app (Req 15.4, consistent with Property 6).



## UI design

### Layout (Textual CSS)
- **Chrome fix**: remove `border` from `Header` and `Footer` (root cause of the empty
  boxes). Keep their background/foreground only.
- `TabbedContent` fills the body; each `TabPane` owns its widgets.
- **Run pane** vertical stack: `StatusPanel` (height 3) → `ProgressBar` (height 1, only
  shown while running) → `RichLog` (flex) → `Input` composer (height 3).
- Concentric-radius discipline (from UI-polish guidance): panels use `round` borders
  with `padding: 1`; nested surfaces keep consistent radius. Chrome (Header/Footer)
  stays border-less so it is not clipped.

### Theme
Register one `Theme` ("studio-dark") via `App.register_theme()` and set `self.theme`,
centralizing the palette (GitHub-dark family already in the file):
`background #0d1117`, `surface/panel #161b22`/`#21262d`, `primary #58a6ff`,
`success #7ee787`, `error #f85149`, `foreground #e6edf3`, muted `#7d8590`.
Log-line styles stay **hex Rich styles** (already fixed) — never CSS class names.

### Status panel content
The Run tab shows status as a **status badge** + **meta line** (two `Static` widgets, as
validated in the prototype):
- Badge: `{icon} {STATUS}  •  {topic}` — ● idle, ⠋ spinner running, ⏸ paused,
  ✓ complete, ✗ error (icon + color, not color alone — Req 6.4).
- Meta line: `⏱ {elapsed} │ seg {cur}/{tot} │ VRAM {vram} │ {engines}`, and the final
  `{output}` path on `complete` (Req 3.2).
- Elapsed uses fixed-width formatting (`mm:ss`/`h m s`) so digits do not jitter
  (tabular-number intent, Req 3.4).

(The Components section names this `StatusPanel` for brevity; it may be implemented as
one composite widget or the badge+meta pair — both satisfy Req 3.)

### Progress bar
`ProgressBar(total=segment_total, show_eta=True)`:
- `segment_total == 0` → indeterminate "planning…" (call `.update(total=None)` or hide
  the bar and show a planning label) per Req 2.2/2.5.
- On each poll: `bar.update(total=segment_total, progress=segment_current)`.
- On `complete`: force `progress = total` (Req 2.4).

### Stats pane (validated 2×2 grid)
`Grid(id="stats_grid")` with `grid-size: 2 2`, `grid-gutter: 1 2`, fixed card
`height: 7`. Four cells:
- **elapsed** — `Static` showing `mm:ss` + ETA (success color), tabular formatting.
- **segments** — `Static` showing `cur / tot` + percent (accent color).
- **throughput** — a `Sparkline` **wrapped in a titled `Vertical` card** (bare
  Sparkline in a Grid overflowed in the prototype) with
  `.sparkline--max-color #7ee787` / `--min-color #1f6feb`.
- **engines** — `Static` listing LLM / TTS / IMG models, plus the VRAM line
  (`UIState.vram_text`) when non-empty (Req 4.2) and the **most recent log line**
  (Req 4.1) as a dim footer row.

Missing values render `—` (Req 4.4). The Stats grid collectively satisfies Req 4.1
(topic/status via the shared status panel; segments/elapsed/last-log in the grid).
`DataTable` remains an acceptable alternative; the card grid was chosen for visual
clarity after seeing both rendered.

### Help pane
Static text listing all keybindings and a one-line note that the TUI must run in a real
terminal (Windows Terminal / PowerShell), not the embedded editor terminal.

## Keybindings (Req 5)

| Key | Action | Notes |
| --- | --- | --- |
| `ctrl+c` / `ctrl+q` | quit | existing |
| `ctrl+l` | clear log | existing |
| `f1` | switch to Help tab | new |
| `f2` | switch to Run tab | new |
| `f3` | switch to Stats tab | new |
| `ctrl+e` | scroll log to latest | new (RichLog `scroll_end`) |
| `f5` | request pause (running) | sets status=paused + active_question via existing path |
| `ctrl+x` | cancel run (confirm) | sets pipeline abort flag; segments skip, checkpoints kept |
| `ctrl+o` | open output folder | `os.startfile(Path(output).parent)`; notice on failure |
| `ctrl+y` | copy output/last log | `App.copy_to_clipboard` (OSC 52) + toast |
| `ctrl+w` | save session log (opt) | writes `logs/tui_session_*.log`; notice on failure |
| `ctrl+p` | command palette | built-in; also lists Start/Pause/Cancel/Open/Clear/Quit + screens |
| `f6` | re-run preflight checks | Phase 3; background thread; no full run started |

Screens added in Phase 3 (Checkpoints, Artifacts, Preflight) are reachable via the
command palette and the screen menu rather than dedicated top-level keys, to keep the
binding set memorable. Keys are introduced per phase: P1 = quit/clear/tabs/scroll;
P2 adds pause/cancel/open/copy/session-log; P3 adds preflight.

State-aware: pressing pause/cancel when not running → `self.notify(...)` no-op (Req 5.5,
12.4). Quitting while a run is active pushes a confirm `ModalScreen` (Req 14.4).
`Footer` shows bindings (chrome border removed so it is visible).

## Error handling

- Keep the existing `sys.excepthook` → `studio_tui_crash.log` (Req 8.2/8.4).
- Keep the `ImportError` → install hint + non-zero exit (Req 8.1).
- All `_poll` widget updates wrapped defensively: a transient `query_one` miss during
  tab construction must not kill the interval. Catch, log to RichLog, continue.
- `launch_tui.ps1` already opens a real terminal (Windows Terminal else PowerShell)
  (Req 8.3) — keep and reference it from the Help pane.
- **Min-size guard (Req 8.5):** handle `on_resize` / check `self.size`; when smaller than
  80×24, show a centered "resize to at least 80×24" `Static` (or `push_screen` a notice)
  and hide the main layout; restore automatically when enlarged. Textual has no built-in
  minimum-size handling, so this is explicit.
- **Completion bell (Req 9):** on the poll tick where `status` first becomes
  `complete`/`error`, call `self.bell()` once. Track the last-seen status so it fires
  exactly once per transition; an unsupported bell is a silent no-op while the toast +
  status panel still convey the change.

## Testing strategy

## Testing strategy

Each phase has its own verification gate; later phases must not regress earlier ones.

### Phase 1 (R1–R9)
1. **Static/headless**
   - `py_compile` `studio_tui.py`, `agents/director_agent.py`, `core/pipeline_long.py`.
   - Import smoke + `StudioTUI()` instantiation (no `.run()`).
   - Unit-test `UIState.set_progress` / `reset_run` defaults and clamping.
2. **Interactive (real PTY via tui harness)**
   - Launch; assert Header title, Footer bindings, and StatusPanel are **non-empty**
     (fixes observed baseline bug).
   - Type a topic → assert status flips to running and a progress/planning indicator
     appears; assert ETC shows a value or `—` (never fabricated).
   - Switch tabs `f1/f2/f3` → assert Stats grid (incl. VRAM gauge) and Help text render.
   - Force a tiny terminal (resize to <80×24) → assert the resize notice shows and the
     layout recovers when enlarged.
   - Drive status → `complete` → assert the bell fires once and progress shows 100%.
3. **Compatibility**
   - Call the `local_ui` status endpoint shape in isolation → confirm it still returns
     `status/active_question/logs/output_video` unchanged.
   - Run a `--dry-run` pipeline WITHOUT the TUI → confirm no behavior change and the new
     `UIState` writes are inert.

### Phase 2 (R10–R15)
   - Options panel: build kwargs from an unchanged panel → assert it equals `{topic}`
     only (defaults preserved); invalid duration / missing file → run blocked with a
     notice.
   - File input: a real temp story file → assert it is read into `content_text` and the
     status shows the file name.
   - Cancel: set abort via the exposed `request_cancel()` → assert `_director_aborted()`
     is True and a subsequent `process_segment` skips; assert no checkpoint files are
     deleted.
   - Confirm-on-quit: with `_pipeline_active=True`, `action_quit` pushes a modal rather
     than exiting; Escape dismisses it.
   - Output access: `os.startfile` patched/mocked → assert it is called with the output
     folder and a failure raises only a toast, not an exception.
   - Session log (optional): `ctrl+w` writes a file and reports its path; a write failure
     surfaces a notice.

### Phase 3 (R16–R18)
   - Preflight: the structured check result is rendered as OK/WARN/FAILED rows; a
     connection error yields a graceful "unavailable" panel, not an exception; re-run
     runs on a background thread.
   - Checkpoints: list parses `studio_checkpoints/*.json`; clear requires confirmation
     and calls `CheckpointManager.clear`; no clear without confirm.
   - Artifacts: malformed/missing `run_manifest.json` / `segment_NN_meta.json` →
     "not available", never a crash; viewing never writes.

## Risks & mitigations

- **Torn scalar reads** across threads → harmless (one stale frame); no locks added to
  keep the hot path simple, matching the existing design.
- **Textual API drift** → mitigated by pinning to 8.2.7 and verifying every widget
  against the venv before coding (done).
- **Terminal can't render** → unchanged failure mode, covered by crash log + launcher.
- **Progress hook placement** → keep writes adjacent to existing `tqdm` accounting so
  they stay correct if the loop is refactored.
- **Scope creep** → overlap policy fixes the boundary (no config/voice/diagnostics in
  TUI); phases keep each delivery small and shippable.

## Risks & mitigations

- **Torn scalar reads** across threads → harmless (one stale frame); no locks added to
  keep the hot path simple, matching the existing design.
- **Textual API drift** → mitigated by pinning to 8.2.7 and verifying every widget
  against the venv before coding (done).
- **Terminal can't render** → unchanged failure mode, covered by crash log + launcher.
- **Progress hook placement** → keep writes adjacent to existing `tqdm` accounting so
  they stay correct if the loop is refactored.
