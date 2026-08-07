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
| 11 | One voice limit (backend 20MB) surfaced in-window; no `alert()` |
| 12, 16 | Refresh controls on read-only panels; single unified log view |
| 13, 22, 23 | Extend `utils/preflight.py` with a **ComfyUI reachability** + **TTS-path** check; run at startup; gate on it |
| 14, 17, 18, 19, 20 | Settings panel with blast-radius warnings; de-absolute `config.yaml` python path; launcher sets CWD + venv python + `sys.path` |
| 29 | Always launch the venv interpreter from the resolved repo root |
| 31, 32 | Remove dormant `audio_fx`/upload/SFX surface from the UI; leave config untouched unless approved |

## Phases

- **Phase 1 — Shell + engine + core loop:** `gui` feature + `Cargo.toml`
  (`eframe = "=0.29"`, glow, MSRV ≤ 1.81); window with navigation rail + status
  rail (backend/worker/ComfyUI/Ollama); engine manager (venv python, CWD, worker
  spawn, port handling); Director Canvas (selected job, output, open/play);
  Create Job (all sources + flags, validated); Jobs (list/detail/cancel/retry/
  live logs). Preflight fix (22/23).
- **Phase 2 — Remaining native panels:** Voice Studio, Artifacts, Memory,
  Characters, Settings (blast-radius + relocation safety).
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
