"""Guards the public import surface during the refactor.

Every symbol that used to be importable from the original modules must stay
importable from the same path (via re-export shims). A forgotten shim fails here.
"""
import importlib

import pytest

EXPECTED = {
    "agents.director_agent": ["DirectorAgent", "UIState", "_devanagari_ratio"],
    "core.segment_runner": [
        "make_process_segment", "build_retry_wrapper",
        "set_director_abort", "get_director_abort", "_director_aborted",
        "start_ollama_server", "stop_ollama_server", "schedule_ollama_stop",
        "touch_ollama_active", "evict_ollama_models", "log_vram_usage",
        "aggressive_vram_cleanup", "_tts_word_budget", "_trim_script_to_word_limit",
        "_perceptual_hash", "_detect_important_trigger",
    ],
    "core.pre_production": [
        "run_pre_production", "plan_outline", "run_preflight_checks",
        "_seed_director_memory", "_deep_merge", "_sanitize_narration",
        "_reject_unsafe_narration", "_normalize_hindi_for_tts",
        "format_time_hms", "format_chapters_time", "get_video_duration",
    ],
    "core.pipeline_long": [
        "run_long_pipeline", "run_long_pipeline_async", "request_cancel",
        "make_process_segment", "_deep_merge", "plan_outline",
    ],
}


@pytest.mark.parametrize("module,names", EXPECTED.items())
def test_public_symbols_still_importable(module, names):
    mod = importlib.import_module(module)
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, f"{module} lost public symbols: {missing}"
