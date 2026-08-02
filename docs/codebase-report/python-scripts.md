# Python Entry Scripts — 2026-08-02

## Root CLI / entry points

- `bootstrap_pipeline.py` — batch pipeline CLI (deepest loop chain, tld 8).
- `core/pipeline_cli.py:main` — long-pipeline CLI entry.
- `jobs/worker.py` + `jobs/run_worker.py` — standalone queue worker.
- `audio/omnivoice_worker.py:main` — TTS worker (persistent/oneshot modes).
- `utils/model_eval.py` — `run_eval` / `run_image_eval` eval entry.

## scripts/ (ops)

- `scripts/comfyui_smoke.py` — AGENTS.md real-instance smoke gate (mandatory after ComfyUI changes).
- `scripts/cleanup_artifacts.py` — artifact GC (dry-run default).
- `scripts/check_environment.py` — env preflight (has tests).

## tools/ (one-shot, documented in `reference_assets/datasets/README.md`)

- `tools/ab_compare_t2i.py` — t2i model A/B comparison.
- `tools/import_manga_panel_dataset.py` — manga panel dataset import.
- `tools/import_anime_face_dataset.py` — anime face dataset import.

## Other

- `setup_youtube_profile.py` — YouTube auth profile setup (Playwright).
- `utils/local_ui.py` — FastAPI dashboard backend (frontend = Vite `dashboard/`).
- `style_resolver.py` — top-level importable lib (not a script).
- Launchers (bat/vbs): `run.bat`, `run_comfyui.bat`, `run_worker.bat`, `open_dashboard.bat`, `stop_studio.bat`, `launch_studio_silent.vbs`.
- Rust twin: `rust/worker/src/main.rs` (Worker run_forever/run_once, doctor).

No scripts found that are orphaned — all have a caller, test, or doc reference.
