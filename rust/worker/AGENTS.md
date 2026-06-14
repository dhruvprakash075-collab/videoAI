# Rust worker crate

This crate is a process supervisor, not an ML component. It only spawns `bootstrap_pipeline.py`.

Never add Torch/ComfyUI/PyO3 deps. Never modify `bootstrap_pipeline.py`, `core/`, `video/`, `audio/`, or the SQLite schema.

Mirror constants exactly: heartbeat 10s, stale 120s, cancel grace 30s, poll 5s.

Always run `cargo clippy -- -D warnings` and `cargo fmt` before declaring done.

If a schema change seems necessary, STOP and ask.
