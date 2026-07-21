"""core.runtime package — Runtime lifecycle management for heavy models.

Submodules:
    ollama: Ollama server lifecycle (start/stop/evict/health-check)
    vram: VRAM logging + aggressive cleanup
    abort: Global Director abort flag (thread-safe)
"""
