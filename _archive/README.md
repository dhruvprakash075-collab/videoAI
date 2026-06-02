# _archive — moved-out artifacts

This directory holds items that were once part of the `C:\Video.AI` working tree
but were moved out to keep the active project clean. Nothing in `_archive/` is
required for `bootstrap_pipeline.py` / `studio_tui.py` / `local_ui.py` to run.

If you need to recover any of these items, move them back to the project root
and update the relevant launcher (run.bat, activate_video_ai.bat).

## Contents

### tts_audiobook/  (moved 2026-06-01)

A **separate audiobook TTS tool** with its own `pyproject.toml`, `package.json`,
`requirements-base.txt` / `requirements-omnivoice.txt`, `README.md`, and full
`docs/` and `tests/` trees. Roughly 219 source files.

Why it lived in this repo: it was originally developed alongside Video.AI and
shares the OmniVoice TTS engine. But it has zero runtime dependency on
`core/`, `agents/`, `video/`, etc. — it is a fully independent project.

To revive as a standalone project:
1. `mv _archive/tts_audiobook ../tts_audiobook`
2. `cd ../tts_audiobook`
3. `uv venv .venv --python 3.12 && uv pip install -r requirements-base.txt`
4. See `_archive/tts_audiobook/README.md` for the original launch instructions.

### pipeline_env/  (moved 2026-06-01)

A ~180 MB Python venv that **was never referenced by any launcher**.
`run.bat` and `activate_video_ai.bat` both activate `venv\`, never `pipeline_env\`.
`bootstrap_pipeline.py` and `studio_tui.py` use `sys.executable` from the active
Python (which is `venv\Scripts\python.exe` when run via the launchers).

Reclaim disk space: `Remove-Item -Recurse -Force _archive\pipeline_env` once you
are confident nothing needs it.

### rvc_env/  (moved 2026-06-01)

A ~680 MB Python venv for **RVC (Retrieval-based Voice Conversion)**. RVC is
opt-in (`rvc.enabled: false` in `config/config.yaml` by default) and is
invoked from `audio/audio_proxy.rvc_convert()` which spawns a subprocess using
its own `rvc_env/Scripts/python.exe` — this **does** still depend on the path.

Reclaim disk space: `Remove-Item -Recurse -Force _archive\rvc_env` only if you
have permanently disabled RVC (`rvc.enabled: false` in config.yaml) and do not
plan to re-enable it. If you ever re-enable RVC, move it back to the project
root: `mv _archive/rvc_env ./rvc_env`.
