# Video.AI

Video.AI is a local Windows-first video generation pipeline. It takes a topic or source document, plans a story, writes segment scripts, generates narration, creates ComfyUI images, renders Ken Burns-style video segments, and assembles a final MP4.

Code is the source of truth. If this README disagrees with `config/config.yaml`, `bootstrap_pipeline.py`, or the Python modules, trust the code.

## Requirements

- Python 3.12 in the project virtual environment
- FFmpeg and FFprobe on `PATH`
- Ollama running at `ollama.host` from `config/config.yaml`
- ComfyUI when using the default `image_gen.backend: comfyui`

## Setup

```powershell
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

Pull the configured Ollama models:

```powershell
ollama pull hermes-director
ollama pull zephyr-writer
ollama pull sarvam-translate
```

The checked-in config currently uses `tts.engine: indicf5`, with IndicF5 paths under `tts.indicf5`. ComfyUI is configured under `image_gen.comfyui` and auto-starts when needed.

## Run

Always use the bootstrap entry point. It applies compatibility setup, PATH fixes, preflight, graceful shutdown hooks, and the venv guard.

```powershell
.\venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your topic"
```

Useful flags are defined in `bootstrap_pipeline.py`:

```powershell
.\venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your topic" --dry-run
.\venv\Scripts\python.exe bootstrap_pipeline.py --source path\to\story.md
.\venv\Scripts\python.exe bootstrap_pipeline.py --preflight-only
.\venv\Scripts\python.exe bootstrap_pipeline.py --sentry-smoke
```

## Local UI

The local FastAPI backend is `utils/local_ui.py`; dashboard assets live in `dashboard/`.

```powershell
.\venv\Scripts\python.exe -m utils.local_ui
```

The backend exposes local-only API routes for jobs, uploads, config, preflight, artifacts, chat, A/B image generation, and manual consultation.

## Rust Worker Sidecar

The Python worker remains the default operational path. The Rust sidecar in `rust/worker` is optional and supervises the existing SQLite job queue.

```powershell
cargo run --manifest-path rust/worker/Cargo.toml -- list-jobs
cargo run --manifest-path rust/worker/Cargo.toml -- doctor
cargo run --manifest-path rust/worker/Cargo.toml -- run --once
cargo run --manifest-path rust/worker/Cargo.toml -- serve
```

The worker resolves Python from `VIDEOAI_PYTHON`, then `venv/Scripts/python.exe` on Windows.

## Verification

Current backend verification:

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m ruff check .
$env:PYTHONPATH="C:\Video.AI\codex_tmp\scanner_pkgs;C:\Video.AI\codex_tmp\checker_deps"; python -m basedpyright
```

Latest local result: `1969 passed, 5 skipped`; Ruff clean; BasedPyright `0 errors, 0 warnings`.

## Live Documentation

- `docs/system_architecture.md`
- `docs/configuration_reference.md`
- `docs/runtime_safety_guide.md`
- `docs/testing_and_linting.md`
- `docs/qwen_image_edit_setup.md`
- `docs/service_instructions.txt`
