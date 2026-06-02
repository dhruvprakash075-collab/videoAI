# Product

Video.AI is a **Dynamic Narrative Video-Generation Engine**. It turns a topic or a story text file into a fully narrated, subtitled, multi-segment video — running entirely on local hardware (tuned for an RTX 4050 6GB laptop GPU).

## What it does

Given a topic (`--topic`) or a story file (`--file`), the pipeline:
1. Plans the story arc and pacing (Director agent).
2. Writes per-segment scripts (Writer agent) and reviews them (Reviewer model).
3. Translates narration (e.g. to Hindi/Devanagari) when configured.
4. Generates voice-over audio (TTS), optional voice conversion (RVC), and SFX/music mixing.
5. Generates per-scene imagery with Stable Diffusion, enforcing character/visual continuity.
6. Renders segments and concatenates them into a final subtitled MP4.

## Key characteristics

- **Local-first / offline**: LLMs run through Ollama; image gen via local Stable Diffusion. No mandatory cloud calls (telemetry is explicitly disabled).
- **Long-form capable**: Designed to produce videos up to ~3 hours via fixed-length segments.
- **Resumable**: Checkpointing lets long runs resume after a crash (critical on constrained GPUs).
- **Continuity-aware**: A persistent story memory tracks characters, recurring symbols, and emotional state across segments.
- **Operator UI**: A local React dashboard plus a localhost-only FastAPI server (`utils/local_ui.py`, port 8000) for control and status.

## Audience

A single operator running the tool on their own Windows machine. Not a multi-tenant or publicly hosted service — the API is bound to `127.0.0.1` by design.
