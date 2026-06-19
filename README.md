# Video.AI

Video.AI is maintained on GitHub.

## Reference Repos

- [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
- [shadcn/improve](https://github.com/shadcn/improve)

Local checkouts live in:

- `external/ponytail`
- `external/shadcn-improve`

## Rust worker sidecar

The Rust worker lives in `rust/worker` as a standalone sidecar binary named `videoai-worker`. It is a process supervisor only: it reads the existing SQLite job queue and spawns `bootstrap_pipeline.py` without importing Python ML components.

Use the read-only CLI to inspect queued jobs:

```bash
cargo run --manifest-path rust/worker/Cargo.toml -- list-jobs
```

The command opens the existing `studio_projects/jobs/video_ai_jobs.db` file read-only, applies a 5000 ms busy timeout, and prints `id`, `status`, `topic`, and `created_at` in the same newest-first order as `JobStore.list_jobs()`. It does not create the database; start the Python app once if the DB does not exist yet.

Run the Rust supervisor explicitly when opting into the sidecar:

```bash
cargo run --manifest-path rust/worker/Cargo.toml -- run
cargo run --manifest-path rust/worker/Cargo.toml -- run --once
```

Run environment checks before a pipeline run:

```bash
cargo run --manifest-path rust/worker/Cargo.toml -- doctor
cargo run --manifest-path rust/worker/Cargo.toml -- doctor --json
cargo run --manifest-path rust/worker/Cargo.toml -- doctor --strict
```

`doctor` is read-only and reports Python, `bootstrap_pipeline.py`, the job database schema and counts, `config.yaml`, ComfyUI reachability/checkpoints when configured, `ffmpeg`, `ffprobe`, disk space, GPU/VRAM via `nvidia-smi` when available, and expected writable directories. Critical failures exit nonzero; `--strict` also treats warnings as failures.

Serve read-only job status endpoints locally:

```bash
cargo run --manifest-path rust/worker/Cargo.toml -- serve
cargo run --manifest-path rust/worker/Cargo.toml -- serve --host 127.0.0.1 --port 8787
```

The status endpoint never mutates the queue database. It opens the existing SQLite file read-only and exposes:

* `GET /healthz` — process liveness
* `GET /readyz` — read-only DB/schema readiness
* `GET /stats` — job counts by status
* `GET /jobs?limit=100&offset=0` — newest-first job list
* `GET /jobs/:id` — one job with events and artifacts

The Python worker remains the default operational path. The Rust worker resolves the interpreter from `VIDEOAI_PYTHON` when set, otherwise `venv/Scripts/python.exe` on Windows and `venv/bin/python` on Unix.

# Setup and Installation

## Requirements
- Python >= 3.10, < 3.14 (Note: Python 3.14 is not supported due to CrewAI incompatibilities).
- FFmpeg and FFprobe (must be on system PATH).

## Installation

1. Create a virtual environment:
   ```bash
   python -m venv venv
   ```

2. Activate the virtual environment:
   - On Windows:
     ```powershell
     .\venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Setup Quickstart

### 1. Ollama Setup
Install Ollama, start it, and pull the required models:
```bash
ollama pull hermes-director
ollama pull zephyr-writer
```

### 2. ComfyUI Setup
If you want to use ComfyUI for image generation:
1. Install ComfyUI.
2. Edit `config/config.yaml` to specify the `comfyui_root`, `comfyui_python` (pointing to the Python executable in your ComfyUI venv), and `comfyui_workflow_path`.

### 3. Config Quickstart
Edit the checked-in `config/config.yaml` to configure local paths and options.

## Testing and Running

### Run the test suite:
```bash
.\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

### Run the pipeline via CLI:
```bash
.\venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your topic here"
```

### Start the Local UI / Studio:
You can start the studio (Ollama, Backend, Frontend, and Worker) using:
```bash
.\launch_studio.bat
```
Alternatively, start the backend server manually:
```bash
.\venv\Scripts\python.exe -m utils.local_ui
```
