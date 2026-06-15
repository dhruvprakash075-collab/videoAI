# Rust worker crate

This crate is primarily a process supervisor for `bootstrap_pipeline.py`, plus approved opt-in sidecar utilities for media, text, audio, checkpointing, final assembly, and Python interop.

Do not add Torch or ComfyUI deps. Do not modify `bootstrap_pipeline.py`, `core/`, `video/`, or the SQLite schema.

Approved exceptions:
- The optional, feature-gated PyO3 bridge (`python-extension`) is allowed for `videoai_worker_native` packaging, CI, and smoke tests.
- Rust audio analysis/mastering work is allowed when it stays opt-in or fallback-safe from Python, preserves existing Python/FFmpeg fallbacks, and avoids default behavior changes until explicitly approved.
- Touching `audio/` Python files is allowed only for Rust interop gates, fallback behavior, parity tests, and safe rollout flags such as `VIDEOAI_RUST_AUDIO`.

Mirror constants exactly: heartbeat 10s, stale 120s, cancel grace 30s, poll 5s.

Always run `cargo clippy -- -D warnings` and `cargo fmt` before declaring done.

If a schema change seems necessary, STOP and ask.

Intentional PR 2 deviation from `jobs/worker.py`: the Python interpreter resolves from `VIDEOAI_PYTHON` when set, otherwise `venv/Scripts/python.exe` on Windows and `venv/bin/python` on Unix so the standalone worker can run cross-platform and in CI.
