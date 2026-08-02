# Execution Log — every entry-point run, with verdict

Interpreter legend: **venv** = `.\venv\Scripts\python.exe` (Python 3.12.13,
the sanctioned interpreter); **system** = `python` on PATH (`C:\Python314`,
3.14.5 — outside the project's `>=3.10,<3.14` range, used only to expose
interpreter drift).

## Static gates

| Command | Result |
|---|---|
| `compileall` over all tracked .py (228 files) | exit 0 — no syntax errors |
| `python -m pytest tests` (system 3.14.5) | 2048 passed, 5 skipped, 1 warning, 26.43 s |
| `cargo test` (rust/worker) | 73 passed (49 lib + 23 main + 1 checkpoint_cli) |
| `cargo clippy -- -D warnings` | clean |
| `cargo fmt --check` | clean |

## Entry points (all at safe boundaries: --help / dry flags)

| Command | Interp | Result | Verdict |
|---|---|---|---|
| `bootstrap_pipeline.py --help` | system | venv-guard error, rc=1 | OK — guard works as designed |
| `bootstrap_pipeline.py --help` | venv | help printed | OK |
| `core/pipeline_cli.py --help` (as script) | venv | `ModuleNotFoundError: No module named 'core'` | BUG #4 (needs `-m`) |
| `python -m core.pipeline_cli --help` | venv | help printed | OK |
| `jobs/worker.py --help` / `-m jobs.worker --help` | venv | **hangs** — poll loop starts, no output, killed after 12 s | BUG #1 |
| `jobs/run_worker.py --help` | venv | `ModuleNotFoundError: No module named 'config'` | BUG #4 |
| `audio/omnivoice_worker.py --help` | system | help printed | OK |
| `audio/indicf5_worker.py --help` | system | help printed | OK |
| `audio/supertonic_worker.py --help` | system | help printed | OK |
| `scripts/check_environment.py` | system | failed (3.14 environment) | interpreter drift, BUG #12 |
| `scripts/check_environment.py` | venv | **8/8 passed** (FFmpeg 8.1.1, Ollama 9 models, VRAM 0.6/6.0 GB warning, disk 64 GB, dirs, config) | OK; VRAM low warning |
| `scripts/cleanup_artifacts.py --days-old 1` | system | listed artifacts, no delete | OK |
| `scripts/comfyui_smoke.py --help` | venv | **ran the real smoke**: live ComfyUI, frame produced `scene_01.png` 533 136 bytes, "SMOKE OK (1 new frame(s))" | BUG #2 — `--help` ignored, real workflow executed (accidental; passed, but crossed the no-real-tools boundary) |
| `tools/ab_compare_t2i.py --help` | system | help printed | OK |
| `tools/import_manga_panel_dataset.py --help` | system | help printed | OK |
| `tools/import_anime_face_dataset.py --help` | system | help printed | OK |
| `setup_youtube_profile.py --help` | system | help printed | OK |
| `utils/model_eval.py --help` | system | help printed | OK |
| `utils/diagnose.py --help` | venv | `[ERROR] Unknown diagnostic command: --help`, rc=1 | BUG #5 |
| `utils/preflight.py` (as script) | system | checks fail: `No module named 'config'` (ollama_ping, director_model); vram 2.6/6.0 < 4.5; playwright 2-command fallback works; net result: ok=3 fail=3 skip=2 | BUG #3 + env VRAM (BUG #13) |
| `utils/preflight.py` (as script) | venv | same `config` failures | BUG #3 confirmed interpreter-independent |
| `utils/local_ui.py --help` (as script) | venv | `No module named 'agents'`, rc=1 | BUG #14 |
| `python -m utils.local_ui --help` | venv | **`--help` ignored — live uvicorn on 127.0.0.1:8000, killed after 60 s, no listener left** | BUG #15 |
| `cargo run --help` (rust/worker) | — | rc=101 "could not determine which binary" | BUG #16 (no `default-run`) |
| `cargo run --bin videoai-worker -- --help` | — | rc=0, 12 subcommands | OK |
| `cargo run --bin videoai-worker -- doctor --json` (from rust/worker) | — | `bootstrap_pipeline` + `config_yaml` false "missing" (CWD-relative root); comfyui skipped "image backend is not comfyui" | BUG #17 |
| `cargo run --bin {audio_analyze,text_split,videoai_checkpoint} -- --help` | — | rc=0, proper clap help | OK |
| `validate_config(config.yaml)` (venv, pydantic 2.12.5) | venv | VALID, 22 sections | OK |
| workflows scan (6 JSON, config/comfyui/workflows) | — | all node classes resolve; seeds fixed (42×5, 0×1) | OK |

## Import smoke (all 228 tracked modules importable)

- venv: **all production modules import clean** — zero import breakage.
- system 3.14: `core/main.py` fails (`No module named 'crewai'` — not
  installed in system python). Tests pass anyway via conftest stubs.
  Confirms interpreter drift (BUG #12) rather than a code bug.

## Side effects created (be transparent)

- One real ComfyUI smoke ran (bug #2 above). Its output frame landed in a
  gitignored output dir — `git status` shows only `?? docs/codebase-report/`.
- One `jobs/worker.py --help` run started a poll loop; killed via
  Stop-Process. No jobs were queued, DB untouched (`studio_projects/jobs/`).
- One `python -m utils.local_ui --help` run started a uvicorn server on
  127.0.0.1:8000 (BUG #15); killed via Stop-Process, verified no listener
  remained. No state written.

## Not executed (per user boundary: no real models/tools)

- No Ollama generation, no ComfyUI *intended* run (except bug #2), no
  YouTube upload, no model eval, no cleanup deletes (`--days-old 1` only
  lists), no dataset imports, no video rendering, no whisper/faster-whisper.
- `--help`-safe entries verified; remaining tools not exercised beyond help.
