# Tech Stack

## Languages & Runtimes

- **Python 3.10–3.13** (NOT 3.14) — the entire pipeline. Virtualenv lives at `venv/`.
- **Node.js / JavaScript (ES modules)** — the `dashboard/` React frontend.
- **PowerShell / Batch** — Windows run scripts at the repo root (`*.ps1`, `run.bat`).

## Python stack

- **CrewAI** — multi-agent orchestration (Director, Writer, Executive agents in `core/main.py`).
- **Ollama** — local LLM serving at `http://localhost:11434`. Models are referenced by name in `config/config.yaml` (e.g. `qwen3.5-9b-opus`, `l3-8b-sunfall`) and must be pulled separately via the Ollama CLI.
- **diffusers** — Stable Diffusion image generation (local, float16, xformers/VAE-tiling for 6GB VRAM).
- **PyTorch + torchaudio** — installed separately with CUDA 12.8 wheels (`--index-url https://download.pytorch.org/whl/cu128`); not pinned in `requirements.txt`.
- **TTS / ASR**: `edge-tts`, OmniVoice (optional), `faster-whisper` (+ `openai-whisper` fallback) for subtitle word timestamps.
- **Audio**: `pydub`, `soundfile`; FFmpeg via the bundled `ffmpeg-8.1.1-essentials_build/`.
- **transformers / peft** — translation and LoRA training (`train_lora.py`).
- **FastAPI + uvicorn** — local control/status API (`utils/local_ui.py`).
- **PyYAML** — config loading; **beautifulsoup4 / requests** — Director web research.

## Frontend stack (dashboard/)

- **React 19** + **Vite 8**, **Tailwind CSS 4**, `lucide-react` icons, ESLint.

## Configuration

- Primary config: `config/config.yaml`, loaded and schema-validated via `config/config.py` (`load_config`).
- Defaults are deep-merged with the YAML, then optionally with `projects/{name}.yaml` (per-series overrides).
- Prompts live in `prompts.yaml`; visual styles in `styles.yaml` (`style_resolver.py`).
- Use `python-dotenv` / env vars for secrets — never hardcode keys.

## Platform notes (important)

- **Windows-first.** Always use Windows-friendly commands (CMD/PowerShell), not bash idioms.
- `bootstrap_pipeline.py` applies compatibility patches: forces UTF-8 console encoding, patches `rich` Win32 console writes, and disables CrewAI/OpenTelemetry telemetry. **Always run through bootstrap**, not the pipeline modules directly.
- `xformers`/`triton` and `torch.compile` are unavailable on Windows — code already guards/suppresses these (`TORCHDYNAMO_SUPPRESS_ERRORS`). Don't reintroduce hard dependencies on them.
- GPU work is memory-constrained (6GB). Be conservative with concurrency, batch sizes, and resolution.

## Common Commands

Run from the repo root, using the venv interpreter.

### Run the pipeline
```powershell
# Via bootstrap (preferred — applies all patches)
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your Topic" --duration 10 --no-resume

# From a story file
venv\Scripts\python.exe bootstrap_pipeline.py --file path\to\story.txt

# Dry run (no video generated)
venv\Scripts\python.exe bootstrap_pipeline.py --topic "Your Topic" --dry-run
```

Useful flags: `--project <series>`, `--series`, `--director-mode` (pause for review), `--skip-rvc`, `--no-resume`.

### Install dependencies
```powershell
pip install -r requirements.txt
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### Local control API
```powershell
venv\Scripts\python.exe utils\local_ui.py   # serves on http://127.0.0.1:8000
```

### Dashboard (run manually, do not background)
```powershell
cd dashboard
npm install
npm run dev      # Vite dev server
npm run build    # production build
npm run lint     # ESLint
```

> Long-running processes (pipeline runs, `npm run dev`, uvicorn) should be started manually by the operator, not as blocking commands.
