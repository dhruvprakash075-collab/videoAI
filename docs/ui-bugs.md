# UI Bugs — Full Register

**Audit source:** `dashboard/*` (React UI), `utils/local_ui.py` (FastAPI backend),
`utils/preflight.py`, `jobs/*`, `config/*`, `rust/worker/*`, and the project
launch scripts. Compiled 2026-08-07.

Every bug below is a code-verified failure or fragility in the current web UI /
launch experience. The native rebuild (`docs/ui-plan.md`) is scoped to fix these.

## A. Launch & orchestration

| # | Bug | Effect |
|---|---|---|
| 1 | `launch_studio_silent.vbs` runs `launch_studio.bat`, which **does not exist** | Double-clicking the "silent launch" does nothing |
| 2 | App = 5 separate `.bat` processes (backend, npm, worker, ComfyUI, stop) | Fragile, order-dependent, console-window spam |
| 3 | Worker never auto-starts; jobs sit `queued` | Jobs never run; UI only *warns* to start another `.bat` |
| 4 | Requires Node/npm + Vite dev server; served `static/index.html` is stale | UI version depends on entry point (`:5173` vs `:8000`) |
| 5 | Port collisions (`:8000`/`:5173`/`:8188`) fail silently | "Nothing starts" with no message |
| 6 | Browser-bound state (polling, chat session, pause/consultation) | Close the browser = lose the control surface |
| 34 | Duplicate launchers `run.bat` and `open_dashboard.bat` (near-identical) | Confusion over which to use |

## B. Job-execution reliability

| # | Bug | Effect |
|---|---|---|
| 7 | Queue not supervised automatically | Jobs never run without a manual worker |
| 8 | Errors swallowed (`console.error`/`catch(() => {})`) | Invisible failures; a failed run shows only a bare log line |
| 9 | Retry/cancel depend on the worker running; no confirmation | Confusing or no-op outcomes |

## C. Feature panels

| # | Bug | Effect |
|---|---|---|
| 10 | Director Canvas shows the *latest* run's output, not the selected job | Wrong output shown when multiple runs exist |
| 11 | Voice upload limit mismatch (UI enforces 10MB, backend allows 20MB); uses browser `alert()` | Inconsistent validation; popup UX |
| 12 | Memory / Characters / Artifacts load once on mount, no refresh | Stale data until tab remount |
| 13 | Preflight only manual; never auto-run at launch | "Ready" reports can mislead |
| 14 | Settings edits `config.yaml` directly with no blast-radius feedback | A mistyped path/checkpoint breaks the next run |
| 15 | `request_json` barely validated (only `run_mode` + `series`) | Flag typos surface minutes into a run |
| 16 | Two disjoint log views (backend StatusTracker vs per-job log) | Disagree about what is happening |

## D. Pipeline / config / portability

| # | Bug | Effect |
|---|---|---|
| 17 | `config.yaml` hardcodes `tts.indicf5.python: C:\Video.AI\venv\Scripts\python.exe` | Relocating the app breaks IndicF5 TTS |
| 18 | Config paths are repo-CWD-relative everywhere | Engine only works if CWD is the repo root |
| 19 | Windows-only backslash path `tts.indicf5.root: external\IndicF5` | Non-portable (violates AGENTS cross-platform rule) |
| 20 | Inconsistent IndicF5 path refs (config vs `audio_proxy.py:138` docstring `D:\IndicF5`) | Confusion |
| 21 | Two competing workers (Python `jobs.run_worker` + Rust `videoai-worker run`) | Double-claim risk; `stop_studio.bat` only hunts Python one |
| 22 | Preflight never checks ComfyUI reachability or TTS engine path | False "OK" → late failure at the image phase |
| 23 | No auto preflight on launch | Readiness not surfaced |
| 24 | 30-min heavy-task timeout with no UI handling | Long runs can error opaquely |
| 29 | Venv guard (`bootstrap_pipeline.py`) hard-fails on wrong interpreter | Native launcher must always use the venv python |
| 30 | Circuit-breaker / 240s LLM timeout silently retries | Stuck "thinking" states with no explanation |
| 33 | `static/index.html` (21,926 B) is a completely different OLD standalone UI vs `dashboard/dist/index.html` (459 B React build) | Version-drift: two entirely different UIs depending on entry point |
| 31 | Dead `audio_fx` config + 9 documented "missing SFX" no-ops (`config.yaml`) | Dead config surface |
| 32 | Dormant YouTube/`upload`/Playwright block (`upload.enabled: false`) | Unused feature area |

## E. Scope trivia (confirmed NON-bugs, so the plan doesn't chase ghosts)

- All config-referenced files exist on disk (voice refs, workflows, checkpoint,
  reference image, panel layouts) — not a missing-file problem on this machine.
- Config schema already registers the `storyboard` section.

## F. Removed-by-scope (residue)

- A/B Testing: `_ab_jobs` in-memory store, `static/ab_picker.html` — feature removed.
- Assistant chat: in-memory sessions (`_chat_sessions`) — feature removed.
- Upload / SFX — dormant, paired cleanup with removed features.

## G. Backend state / status bugs (verified 2026-08-07)

| # | Bug | Evidence | Effect |
|---|---|---|---|
| 37 | `UIState.status` defaults to `"running"` and nothing ever sets it to `idle` | `agents/ui_state.py:71` (`status = "running"`); grep shows assignments only to running/paused/complete/error — never idle | A fresh backend with no job reports `/api/status == "running"` → the UI shows a pulsing green "running" + "Pause Engine" at idle |
| 38 | `UIState.output_video` is set in exactly one place — `utils/local_ui.py:312` inside `run_pipeline_thread` | grep: only assignment is local_ui.py:312 | Jobs run via the worker (`jobs/worker.py`) never set `output_video`, so `/api/status` returns empty even after a successful worker run → Director Canvas stays on the upload card despite a real `output_path` on the job |
| 39 | Inconsistent log truncation | `_uistate_log` truncates front (`logs[100:]`), `add_log` truncates back (`logs[-maxlen:]`) | Minor: differing tail semantics if both are ever mixed |

**Retractions (checked and NOT bugs)**
- No false "stale running → failed": the Python worker heartbeats periodically (`_heartbeat_loop`, 10s) independent of log output, and `mark_stale_running_failed` is only called at `local_ui` startup.
- `request_cancel` does handle `queued` jobs (`STATUS_QUEUED` is in the allowed set).
- UI → `bootstrap_pipeline.py` CLI flags are consistent (no instant-argparse-fail).

## H. Settings / config UI bugs (verified 2026-08-07)

| # | Bug | Evidence | Effect |
|---|---|---|---|
| 40 | **Settings save is structurally broken** — the frontend never sends the required `default_images_per_segment` Form field | `dashboard/src/components/ControlPanel.jsx` `handleSave` builds FormData with voice_engine, dynamic_subtitles, image_backend, composition_mode, all comfyui_* — but **not** `default_images_per_segment`; backend `save_ui_config` declares it `default_images_per_segment: int = Form(...)` (required) | `POST /api/config` returns **422** on every save → UI shows "Failed to save settings" → **no config change made in Settings is ever persisted** |
| 41 | Even absent the 422, the "Images Per Segment" slider value (`maxImagesPerSegment`) is never in the save payload | `handleSave` (above); slider writes `config.maxImagesPerSegment` which is not POSTed | Slider edits would be silently discarded |
| 42 | "Uncapped Scaling" toggle is dead UI | `uncappedScaling` exists only in `DEFAULT_CONFIG`; never read from GET `/api/config` and never sent on save | Toggling it does nothing |

**Consultation contract (kept feature) confirmed:** `consult_user` sets `active_question`, flips `status=paused`, clears+polls `pause_event.wait(timeout=300)`; no reply in 300s → auto-defaults and resumes. `--yes` skips prompting. A native pause modal must surface this 300s window. (`agents/director/consultation.py:19-57`)
