"""core package - Video.AI pipeline core modules.

Submodules:
    pipeline_long: Main orchestration (run_long_pipeline)
    pre_production: Director research, consultation, config overlay
    segment_runner: Per-segment script/TTS/image/render loop
    post_production: Final concat, thumbnail, QC, chapters, manifest
    pipeline_cli: Legacy CLI entrypoint
    director_memory: StoryMemory/WorldState seeding
    decision_record: DecisionRecord build + persist
    preflight: Health checks (Ollama, FFmpeg, VRAM, disk)
    preview: Preview gate after segment 1
    outline_shaping: Outline shaping from DecisionRecord
    segment/*: Graph nodes, budget, identity, retry
    runtime/*: Ollama lifecycle, VRAM, abort flag
"""
