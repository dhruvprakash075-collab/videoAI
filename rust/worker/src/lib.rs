//! `videoai_worker` - Rust sidecar for the Video.AI pipeline.
//!
//! This crate is a process supervisor for `bootstrap_pipeline.py` plus
//! opt-in sidecar utilities for media, text, audio, checkpointing, and
//! FFmpeg planning. It does NOT depend on torch, ComfyUI, or Python
//! internals — it only calls the Python bootstrap as a subprocess.
//!
//! Submodules:
//! - `assets`: Inspect & hash output artifacts (images, audio, video)
//! - `audio`: Analyze / master WAV files (loudnorm, clipping, duration)
//! - `checkpoint`: Crash-safe JSON checkpoint state machine
//! - `ffmpeg_exec`: Execute FFmpeg plans with structured logging
//! - `ffmpeg_plan`: Build concat/loudnorm/ducking filter graphs
//! - `media`: Inspect video files (resolution, FPS, duration, drift)
//! - `text`: Split source text into per-segment chunks (chapter/word/LLM)
//!
//! Constants (mirrored from Python, MUST stay in sync):
//! - HEARTBEAT_INTERVAL_SECONDS = 10
//! - STALE_JOB_SECONDS = 120
//! - CANCEL_GRACE_SECONDS = 30
//! - POLL_INTERVAL_SECONDS = 5
//!
//! The optional `python-extension` feature gates the PyO3 bridge used
//! only for `videoai_worker_native` packaging and CI smoke tests.

pub mod assets;
pub mod audio;
pub mod checkpoint;
pub mod ffmpeg_exec;
pub mod ffmpeg_plan;
pub mod media;
pub mod text;

#[cfg(feature = "python-extension")]
mod python;
