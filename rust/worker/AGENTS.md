# Rust worker crate

This crate is a process supervisor, not an ML component. It only spawns `bootstrap_pipeline.py`.

Never add Torch/ComfyUI/PyO3 deps. Never modify `bootstrap_pipeline.py`, `core/`, `video/`, `audio/`, or the SQLite schema.

Mirror constants exactly: heartbeat 10s, stale 120s, cancel grace 30s, poll 5s.

Always run `cargo clippy -- -D warnings` and `cargo fmt` before declaring done.

If a schema change seems necessary, STOP and ask.

Intentional PR 2 deviation from `jobs/worker.py`: the Python interpreter resolves from `VIDEOAI_PYTHON` when set, otherwise `venv/Scripts/python.exe` on Windows and `venv/bin/python` on Unix so the standalone worker can run cross-platform and in CI.
