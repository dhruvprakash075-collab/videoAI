# UI Plan — Native Windows Rebuild

**Scope:** replace the web dashboard / `.bat`-launch experience with a single
self-managing native Windows app. **Remove** A/B Testing (feature 3) and
Assistant chat (feature 5). **Fix** the bug classes documented in
`docs/ui-bugs.md` (reference by `#<n>`). Companion doc: `docs/ui-bugs.md`.

## Goal

One double-click → one native window that owns the engine: backend + worker +
ComfyUI lifecycle, real error surface, "open output in Explorer/play", and no
browser / npm / console windows. The web frontend stays as a working fallback.

## Architecture (real-world feasible)

- **Frontend:** native `eframe`/`egui` desktop window (feature-gated `gui` in
  `rust/worker`), navigation rail for: Director Canvas, Create Job, Jobs,
  Voice Studio, Artifacts, Preflight, Memory, Characters, Settings.
- **Engine:** the app manages the existing Python backend (`utils/local_ui.py`)
  + worker as subprocesses. **Not** a web wrap — a true native window that calls
  the same proven API/business logic. Rust-native in-process routes (rusqlite
  job/status reads, `assets`/`media` inspection, FFmpeg thumbnails) are used
  where they avoid HTTP.
- **Non-goals:** no pure-Rust rewrite of image-gen/TTS/agents (Python-bound);
  `bootstrap_pipeline.py`, `core/`, `video/`, and the SQLite schema stay
  untouched (AGENTS.md).

## Bug-fix mapping (all from `docs/ui-bugs.md`)

| Bug(s) | Fix in native app |
|---|---|
| 1, 2, 5, 34 | One launcher → app owns backend+worker+ComfyUI; no console/browser; detect ports and bind gracefully; retire duplicate `.bat`/`.vbs` |
| 3, 7, 9, 21 | Auto-drive the queue (spawn worker-once), single worker, confirm cancel/retry in-window |
| 4, 33 | Serve/use only the current React build (or the native shell's own views); drop old `static/index.html` + `static/ab_picker.html`; no npm at runtime |
| 6 | Native window holds all state; no browser dependency |
| 8, 15, 24, 30 | Validate `request_json` at submit; surface errors, breaker-cooldown, and 30-min timeout states in-window |
| 10 | Director Canvas bound to the selected job's output + thumbnail + Open-in-Explorer/Play |
| 37, 38 | Fix backend status contract: default `UIState.status` to `idle`; derive the Canvas preview from the **selected job's `output_path`** (not the web-thread-only `UIState.output_video`) so worker-driven jobs show output too |
| 11 | One voice limit (backend 20MB) surfaced in-window; no `alert()` |
| 12, 16 | Refresh controls on read-only panels; single unified log view |
| 13, 22, 23 | Extend `utils/preflight.py` with a **ComfyUI reachability** + **TTS-path** check; run at startup; gate on it |
| 14, 17, 18, 19, 20 | Settings panel with blast-radius warnings; de-absolute `config.yaml` python path; launcher sets CWD + venv python + `sys.path` |
| 40, 41, 42 | **Fix Settings save for real** — include the images-per-segment field(s) in the payload, drop the dead "Uncapped Scaling" toggle, and surface validation errors in-window (the web Settings save currently 422s every time, so nothing is ever persisted) |
| 29 | Always launch the venv interpreter from the resolved repo root |
| 31, 32 | Remove dormant `audio_fx`/upload/SFX surface from the UI; leave config untouched unless approved |

## New features (folded into build)

Selected by the user. Each reuses existing code (no new heavy deps); `#` are the
feature numbers from the original shortlist. (Feature 8 — Output Compare — not
selected.)

| # | Feature | Reuses | Effort |
|---|---|---|---|
| 1 | **Native Script/Story Editor + per-segment iterate** — edit a segment's script, re-run TTS or re-render just that segment, then re-assemble | `core/segment_runner.py`, checkpointing, `audio/audio_proxy.py` | High |
| 2 | **Series / Project Studio** — create/resume multi-episode `project` runs with continuity (locked characters, visual style, plot threads); browse per-project memory | `run_mode=project`, `memory/project_store.py` | High |
| 3 | **Batch Queue** — import `.txt` of topics, enqueue all, watch aggregate progress | `--topics-file`, job queue | Small |
| 4 | **Storyboard Review & character approval** — view generated sheets, approve/reject panels per segment (feeds approved/rejected galleries + memory) | `core/storyboard.py`, character store | Medium |
| 5 | **TTS Audition** — pick voice+engine+language, generate a short sample and play it in-app before committing | `audio_proxy.generate`, preview route | Medium |
| 6 | **Input-source enrichment** — add a URL/PDF/DOCX source; pipeline researches + outlines | `research` + `source` modules (already configured) | Medium |
| 7 | **Style Packs / presets** — curated `visual.style` + `image_gen` combos (manga, cinematic, anime) | `style_resolver.py` | Small |
| 9 | **Live telemetry rail** — VRAM, disk, Ollama model state, ComfyUI health, heavy-task slot | `UIState.vram_text`, preflight, eviction | Small |* |
| 10 | **Degradation ledger viewer** — surface silent B2 quality fallbacks in a list | `UIState.degradations` (B2) | Small |
| 11 | **ETA / progress forecast** — live per-run ETA from segment counters + start time | `UIState.segment_current/total`, `run_start_ts` | Small |
| 12 | **Windows toast on completion** — notify when a long run finishes | OS toast (no pkg) + job events | Small |

\* `9` depends on the telemetry being surfaced via the status rail (already planned in Phase 1).

## Navigation rail (final)

Director Canvas · Create Job · Jobs · **Batch** (3) · **Series Studio** (2) ·
**Story Editor** (1) · **Storyboard Review** (4) · Voice Studio (+ **TTS Audition** 5) ·
Artifacts · Preflight · Memory · Characters · Settings · **Sources** (6) ·
**Style Packs** (7) — plus a persistent **status rail** (9) and **activity/degradation**
panel (10, 11), and **toast notifications** (12).

## Phases

- **Phase 1 — Shell + engine + core loop:** `gui` feature + `Cargo.toml`
  (`eframe = "=0.29"`, glow, MSRV ≤ 1.81); window with navigation rail + status
  rail (backend/worker/ComfyUI/Ollama) + telemetry (9); engine manager (venv python, CWD, worker
  spawn, port handling); Director Canvas (selected job, output, open/play);
  Create Job (all sources + flags, validated); Jobs (list/detail/cancel/retry/
  live logs); Batch queue (3); toast on completion (12); ETA/progress (11);
  degradation ledger (10). Preflight fix (22/23).
- **Phase 2 — Creation/iteration panels:** Voice Studio + TTS Audition (5),
  Artifacts, Sources (6), Style Packs (7), Storyboard Review (4), Memory,
  Characters, Story Editor + per-segment iterate (1), Series/Project Studio (2),
  Settings (blast-radius + relocation safety, 40/41/42).
- **Phase 3 — Cleanup + gate:** delete old `static/` dashboard + `ab_picker`
  (33) where safe, retire duplicate launchers (34); nail down errors/refresh;
  run `cargo test && cargo clippy -- -D warnings && cargo fmt --check`.

## Definition of done / validation

- Native window launches the engine without any `.bat`, console, or browser.
- A job created in the window runs to completion, with live logs, and the output
  opens in Explorer/player.
- The removed features (A/B, chat) are absent from the UI.
- Rust gate green (`cargo test && clippy -D warnings && fmt --check`); Python
  tests still pass; `ruff check .` clean.
