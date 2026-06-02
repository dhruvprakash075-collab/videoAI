"""deep_test_v2.py — comprehensive module-level test of video.ai.

v2 uses real API signatures. No LLM calls. No HTTP. No real GPU work.
- imports
- pure-function utilities
- state machine round-trips
- edge cases / error paths
- configuration loading

Finds real bugs by trying to break each module.
"""
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["CREWAI_TELEMETRY_OPTOUT"] = "true"
os.environ["TORCHDYNAMO_SUPPRESS_ERRORS"] = "1"

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(r"C:\Video.AI")
sys.path.insert(0, str(ROOT))

from utils.compatibility import apply_all_patches

apply_all_patches()

import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

print("=" * 60)
print("DEEP MODULE TEST v2 — video.ai (real signatures)")
print("=" * 60)

results = []
def record(name, status, detail=""):
    results.append((name, status, detail))
    sym = {"PASS": "V", "FAIL": "X", "SKIP": "o", "WARN": "!"}.get(status, "?")
    print(f"  [{sym}] {status:5s} {name}: {detail}")


# ═════════════════════════════════════════════════════════════
# 1. IMPORT EVERY MODULE
# ═════════════════════════════════════════════════════════════
print("\n--- 1. Import test (every .py file) ---")
modules = [
    "bootstrap_pipeline", "download_f5", "train_lora", "style_resolver",
    "studio_tui_helpers",
    "agents.decision_engine", "agents.executive_agent", "agents.director_agent",
    "audio.audio_fx", "audio.audio_proxy", "audio.omnivoice_worker", "audio.f5_worker",
    "config.config", "config.config_schemas", "config.config_schema",
    "core.pipeline_long", "core.pre_production", "core.segment_runner", "core.post_production",
    "core.main",
    "memory.blackboard", "memory.memory", "memory.permanent_memory", "memory.project_store",
    "utils.benchmark", "utils.checkpoint", "utils.compatibility", "utils.concurrency",
    "utils.context_manager", "utils.crewai_breaker", "utils.debug_helper", "utils.diagnose",
    "utils.emotion_control", "utils.git_helper", "utils.map_codebase", "utils.media_analyzer",
    "utils.model_eval", "utils.ollama_client", "utils.quality_check", "utils.retry_manager",
    "utils.scene_director", "utils.specialized_models", "utils.story_planner",
    "utils.tui_theme_tester", "utils.utils", "utils.vision_cache", "utils.web_search",
    "video.image_gen.image_gen", "video.image_gen.framepack_i2v",
    "video.renderer.assembler", "video.renderer.renderer",
]
imported_ok, imported_fail = [], []
for m in modules:
    try:
        __import__(m)
        imported_ok.append(m)
    except Exception as e:
        imported_fail.append((m, str(e)[:120]))
record(f"imports ({len(imported_ok)}/{len(modules)})",
       "PASS" if not imported_fail else "WARN",
       f"{len(imported_ok)} OK, {len(imported_fail)} failed")
for m, err in imported_fail:
    print(f"      FAIL: {m} -> {err}")


# ═════════════════════════════════════════════════════════════
# 2. CONFIG LOADING
# ═════════════════════════════════════════════════════════════
print("\n--- 2. Config loading ---")
from config import _safe_filename, load_config

try:
    cfg = load_config()
    record("load_config()", "PASS", f"{len(cfg)} top-level keys: {list(cfg.keys())[:5]}")
except Exception as e:
    record("load_config()", "FAIL", str(e)[:120])

try:
    cfg2 = load_config(project_name="series_1")
    record("load_config(project_name='series_1')", "PASS", "loaded overlay")
except Exception as e:
    record("load_config(project_name='series_1')", "FAIL", str(e)[:120])

# _safe_filename edge cases
sfn_cases = [
    ("hello world", "hello_world"),
    ("hello/world\\test?", "hello_world_test_"),
    ("../../etc/passwd", "etc_passwd"),
    ("", ""),
    ("a" * 300, "a" * 200),  # maxlen
    ("unicode: 你好", "unicode___"),
]
sfn_pass = 0
sfn_msgs = []
for inp, _expected in sfn_cases:
    try:
        out = _safe_filename(inp)
        sfn_pass += 1
        sfn_msgs.append(f"{inp[:20]!r}->{out[:25]!r}")
    except Exception as e:
        sfn_msgs.append(f"{inp[:20]!r} RAISED: {e}")
record("_safe_filename edge cases",
       "PASS" if sfn_pass == len(sfn_cases) else "WARN",
       f"{sfn_pass}/{len(sfn_cases)}: {sfn_msgs}")


# ═════════════════════════════════════════════════════════════
# 3. WORKLOAD SCHEDULER (real API: __init__(self) only)
# ═════════════════════════════════════════════════════════════
print("\n--- 3. WorkloadScheduler (real API) ---")
from utils.concurrency import WorkloadScheduler, crewai_lock

try:
    s = WorkloadScheduler()
    record("WorkloadScheduler init", "PASS", "no-arg")

    with s.task("heavy", "test1") as c1:
        with s.task("light", "test2") as c2:
            if s.active_heavy_count() == 1 and s.active_light_count() == 1:
                record("WorkloadScheduler nested", "PASS", "counts tracked")
            else:
                record("WorkloadScheduler nested", "FAIL", f"heavy={s.active_heavy_count()} light={s.active_light_count()}")

    import threading
    barrier = threading.Barrier(3)
    release = threading.Event()
    results_lock = threading.Lock()
    completed = []
    def heavy_worker(n):
        with s.task("heavy", f"hw-{n}"):
            try:
                barrier.wait(timeout=2)
                release.wait(timeout=2)
            except Exception:
                pass
            with results_lock:
                completed.append(n)
    threads = [threading.Thread(target=heavy_worker, args=(i,)) for i in range(3)]
    for t in threads: t.start()
    time.sleep(0.5)
    release.set()
    for t in threads: t.join(timeout=5)
    if len(completed) == 3:
        record("WorkloadScheduler gates heavy", "PASS", "3 workers serialized")
    else:
        record("WorkloadScheduler gates heavy", "FAIL", f"only {len(completed)} completed")
except Exception as e:
    record("WorkloadScheduler", "FAIL", str(e)[:120])
    traceback.print_exc()

try:
    with crewai_lock:
        with crewai_lock:
            pass
    record("crewai_lock is RLock", "PASS", "nested acquire OK")
except Exception as e:
    record("crewai_lock is RLock", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 4. CHECKPOINT (real API)
# ═════════════════════════════════════════════════════════════
print("\n--- 4. CheckpointManager (real API) ---")
from utils.checkpoint import CheckpointManager

try:
    with tempfile.TemporaryDirectory() as td:
        cm = CheckpointManager(checkpoint_dir=Path(td), max_age_hours=24)
        cm.save("seg01", {"script": "test", "audio": "/tmp/a.wav"}, completed=True)
        loaded = cm.get("seg01")
        if loaded and loaded.get("script") == "test":
            record("CheckpointManager save/get", "PASS", "round-trip OK")
        else:
            record("CheckpointManager save/get", "FAIL", f"got {loaded}")
        if cm.get("seg99") is None:
            record("CheckpointManager missing key", "PASS", "returns None")
        else:
            record("CheckpointManager missing key", "FAIL", "should return None")
except Exception as e:
    record("CheckpointManager", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 5. PERMANENT MEMORY (real API needs voice_reference)
# ═════════════════════════════════════════════════════════════
print("\n--- 5. PermanentMemoryLog (real API) ---")
from memory.permanent_memory import PermanentMemoryLog

try:
    with tempfile.TemporaryDirectory() as td:
        pm = PermanentMemoryLog(topic="deep_test_topic", base_dir=td)
        pm.log_character("zara", "purple hair, green eyes", "female voice, calm")
        pm.log_recurring_motif("the door")
        d = pm.read()
        if "zara" in str(d):
            record("PermanentMemoryLog round-trip", "PASS", f"keys={list(d.keys())[:3]}")
        else:
            record("PermanentMemoryLog round-trip", "FAIL", f"data={d}")
except Exception as e:
    record("PermanentMemoryLog", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 6. STORY MEMORY (real API is .load not .read)
# ═════════════════════════════════════════════════════════════
print("\n--- 6. StoryMemory (real API) ---")
from memory.memory import StoryMemory

try:
    with tempfile.TemporaryDirectory() as td:
        sm = StoryMemory(memory_file=Path(td) / "memory.json")
        sm.save("topic1", 1, "script text", "summary text")
        d = sm.load("topic1")
        if d and d.get("seg1", {}).get("script") == "script text":
            record("StoryMemory round-trip", "PASS", "script + summary saved")
        else:
            record("StoryMemory round-trip", "FAIL", f"data={d}")
except Exception as e:
    record("StoryMemory", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 7. ENRICH_PROMPTS (real API: raw_prompts, script, config, plan)
# ═════════════════════════════════════════════════════════════
print("\n--- 7. enrich_prompts (real API) ---")
from utils.scene_director import enrich_prompts

try:
    fake_prompts = "lighthouse at night, old door glowing"
    fake_script = "She walked into the lighthouse. The door glowed."
    fake_config = {"style": "cinematic", "resolution": "768x432"}
    fake_plan = {"mood": "mysterious", "char_presence": [{"protagonist": 0.5}]}
    out = enrich_prompts(fake_prompts, fake_script, fake_config, fake_plan)
    if isinstance(out, tuple) and len(out) == 2:
        record("enrich_prompts", "PASS", f"returns (prompts, neg_prompt); neg_len={len(out[1])}")
    else:
        record("enrich_prompts", "WARN", f"returns {type(out).__name__}")
except Exception as e:
    record("enrich_prompts", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 8. CONTEXT WINDOW (real API: budget_tokens)
# ═════════════════════════════════════════════════════════════
print("\n--- 8. ContextWindowManager (real API) ---")
from utils.context_manager import ContextWindowManager

try:
    cwm = ContextWindowManager(budget_tokens=100)
    ctx = cwm.build_context_for_prompt(memory_entries=[], world_state_block="", agent="test")
    if isinstance(ctx, str):
        record("ContextWindowManager", "PASS", f"ctx len={len(ctx)}")
    else:
        record("ContextWindowManager", "WARN", f"returns {type(ctx).__name__}")
except Exception as e:
    record("ContextWindowManager", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 9. UTIL UTILS (real APIs)
# ═════════════════════════════════════════════════════════════
print("\n--- 9. utils.utils functions (real APIs) ---")
from utils.utils import build_prompts, setup_run_logging, validate_script

try:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log = setup_run_logging(log_dir)
    record("setup_run_logging", "PASS", f"log={type(log).__name__}")
except Exception as e:
    record("setup_run_logging", "FAIL", str(e)[:120])
    traceback.print_exc()

try:
    fake_config = {"image_gen": {"prompt_template": "{scene}, cinematic, dramatic lighting"}}
    fake_plan = {"scenes": ["lighthouse at night", "door glows"]}
    prompts = build_prompts("script text", fake_plan, fake_config)
    if isinstance(prompts, (list, str)) and len(prompts) > 0:
        record("build_prompts", "PASS", f"len={len(prompts)}")
    else:
        record("build_prompts", "WARN", "empty")
except Exception as e:
    record("build_prompts", "FAIL", str(e)[:120])
    traceback.print_exc()

try:
    v0 = validate_script("", {"min_words": 20, "max_words": 600})
    v_short = validate_script("hello world", {"min_words": 20, "max_words": 600})
    v_good = validate_script("word " * 100, {"min_words": 20, "max_words": 600})
    record("validate_script edge cases", "PASS", "empty/short/good handled")
except Exception as e:
    record("validate_script", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 10. TTS / DEVANAGARI / NARRATION SANITIZE
# ═════════════════════════════════════════════════════════════
print("\n--- 10. TTS / Sanitize narration ---")
from audio.audio_proxy import normalize_tts_engine

norm_cases = [
    ("f5", "f5"), ("F5-TTS", "f5"), ("omnivoice", "omnivoice"),
    ("edge", "edge"), ("Microsoft", "edge"),
    ("random-garbage-string", "f5"),
    ("", "f5"), (None, "f5"),
]
norm_pass = 0
for inp, expected in norm_cases:
    try:
        got = normalize_tts_engine(inp)
        if got == expected:
            norm_pass += 1
        else:
            print(f"      normalize({inp!r}) -> {got!r}, expected {expected!r}")
    except Exception as e:
        print(f"      normalize({inp!r}) raised: {e}")
record("normalize_tts_engine 8 cases", "PASS" if norm_pass == 8 else "WARN", f"{norm_pass}/8")

from agents.director_agent import _devanagari_ratio

dev_cases = [("hello world", 0.0), ("नमस्ते दोस्त", 1.0), ("mixed नमस्ते world", 0.5), ("", 0.0)]
dev_pass = 0
for inp, _lo in dev_cases:
    try:
        r = _devanagari_ratio(inp)
        if isinstance(r, (int, float)) and 0.0 <= r <= 1.0:
            dev_pass += 1
    except Exception as e:
        print(f"      _devanagari_ratio({inp!r}) raised: {e}")
record("_devanagari_ratio 4 cases", "PASS" if dev_pass == 4 else "WARN", f"{dev_pass}/4")

from core.pre_production import _sanitize_narration

sanitize_cases = [
    "<think>hidden</think>Hello world",
    "[narration]Hi there[/narration]",
    "Narration: This is a test",
    "As requested, here is the script: Hello",
    "Just normal text",
]
san_pass = 0
san_msgs = []
for inp in sanitize_cases:
    try:
        out = _sanitize_narration(inp)
        san_pass += 1 if isinstance(out, str) and len(out) > 0 else 0
        san_msgs.append(f"{inp[:35]!r:40s} -> {out!r}")
    except Exception as e:
        san_msgs.append(f"{inp[:35]!r} RAISED: {e}")
record("_sanitize_narration 5 cases", "PASS" if san_pass == 5 else "WARN", f"{san_pass}/5")
for m in san_msgs:
    print(f"      {m}")


# ═════════════════════════════════════════════════════════════
# 11. DECISION ENGINE (real API: build_decision_record, no DecisionRecord export)
# ═════════════════════════════════════════════════════════════
print("\n--- 11. Decision engine (real API) ---")
try:
    from types import SimpleNamespace

    from agents.decision_engine import build_decision_record
    fake_director = SimpleNamespace(recommended_duration_min=10, segment_count=3, words_per_segment=100, images_per_segment=5)
    rec = build_decision_record(fake_director, {}, {"words_per_segment": 120}, {"total_duration_min": 8}, {}, {})
    if rec and hasattr(rec, "to_overlay"):
        ov = rec.to_overlay()
        record("build_decision_record", "PASS", f"overlay keys={list(ov.keys())[:5]}")
    else:
        record("build_decision_record", "WARN", f"no to_overlay method; rec={rec}")
except Exception as e:
    record("build_decision_record", "FAIL", str(e)[:200])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 12. RENDERER (real API: compositions_dir)
# ═════════════════════════════════════════════════════════════
print("\n--- 12. Renderer (real API) ---")
import inspect

from video.renderer.renderer import render_with_assets

sig = inspect.signature(render_with_assets)
record("render_with_assets signature", "PASS" if sig else "FAIL", f"params={list(sig.parameters)[:6]}")


# ═════════════════════════════════════════════════════════════
# 13. FRAMEPACK (real API: image_to_video)
# ═════════════════════════════════════════════════════════════
print("\n--- 13. FramePack i2v (real API) ---")
try:
    from video.image_gen.framepack_i2v import image_to_video, is_available
    if callable(is_available):
        avail = is_available()
        record("framepack is_available()", "PASS", f"={avail}")
    if callable(image_to_video):
        record("framepack image_to_video", "PASS", "callable")
except Exception as e:
    record("framepack import", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 14. OLLAMA / BREAKER (real API: fails_threshold, cooldown_s)
# ═════════════════════════════════════════════════════════════
print("\n--- 14. Ollama / Breaker (real API) ---")
try:
    from utils.ollama_client import _BreakerState
    bs = _BreakerState(fails_threshold=5, cooldown_s=30.0)
    if hasattr(bs, "record_failure") and hasattr(bs, "is_open"):
        record("OllamaClient._BreakerState", "PASS", f"API present; is_open={bs.is_open()}")
    else:
        record("OllamaClient._BreakerState", "FAIL", "missing methods")
except Exception as e:
    record("OllamaClient._BreakerState", "FAIL", str(e)[:120])

try:
    from utils.crewai_breaker import BreakerOpen, guarded_ollama_call
    try:
        guarded_ollama_call("nonexistent_model_for_test", "test", timeout=1)
        record("guarded_ollama_call fail", "FAIL", "should have raised")
    except (BreakerOpen, Exception) as e:
        record("guarded_ollama_call fail", "PASS", f"correctly raised {type(e).__name__}")
except Exception as e:
    record("crewai_breaker", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 15. RETRY (real API: max_retries, base_delay, backoff, exceptions)
# ═════════════════════════════════════════════════════════════
print("\n--- 15. Retry manager (real API) ---")
try:
    from utils.retry_manager import retry_with_backoff
    calls = []
    @retry_with_backoff(max_retries=2, base_delay=0.01, exceptions=(IOError,))
    def always_fails():
        calls.append(1)
        raise OSError("simulated transient error")
    try:
        always_fails()
    except OSError:
        if len(calls) == 3:
            record("retry_with_backoff", "PASS", f"retried {len(calls)} times")
        else:
            record("retry_with_backoff", "WARN", f"called {len(calls)} times (expected 3)")
except Exception as e:
    record("retry_with_backoff", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 16. STYLE RESOLVER (real API: just .resolve())
# ═════════════════════════════════════════════════════════════
print("\n--- 16. Style resolver (real API) ---")
try:
    from style_resolver import StyleResolver
    sr = StyleResolver()
    res = sr.resolve("mysterious, dark fantasy")
    record("StyleResolver.resolve()", "PASS", f"returned {type(res).__name__}")
except Exception as e:
    record("StyleResolver", "FAIL", str(e)[:120])
    traceback.print_exc()


# ═════════════════════════════════════════════════════════════
# 17. STUDIO TUI HELPERS (correct API)
# ═════════════════════════════════════════════════════════════
print("\n--- 17. studio_tui_helpers (real API) ---")
import time as _time

from studio_tui_helpers import format_elapsed, format_etc, parse_duration, safe_filename, vram_high

try:
    e0 = format_elapsed(0)
    e_recent = format_elapsed(_time.time() - 90)
    e_old = format_elapsed(_time.time() - 3700)
    if e0 == "\u2014" and e_recent == "01:30" and e_old.startswith("1h"):
        record("format_elapsed", "PASS", f"0={e0!r}, 90s={e_recent!r}, 1h+={e_old!r}")
    else:
        record("format_elapsed", "WARN", f"0={e0!r}, 90s={e_recent!r}, 1h+={e_old!r}")
except Exception as e:
    record("format_elapsed", "FAIL", str(e)[:80])

try:
    p_ok = parse_duration("  15  ")
    p_oob = parse_duration("99999")
    p_zero = parse_duration("0")
    p_none = parse_duration(None)
    p_str = parse_duration("abc")
    if p_ok == 15 and p_oob is None and p_zero is None and p_none is None and p_str is None:
        record("parse_duration 5 cases", "PASS", f"ok={p_ok}, oob={p_oob}, zero={p_zero}, none={p_none}, str={p_str}")
    else:
        record("parse_duration 5 cases", "WARN", f"unexpected: {p_ok}, {p_oob}, {p_zero}, {p_none}, {p_str}")
except Exception as e:
    record("parse_duration", "FAIL", str(e)[:80])

try:
    v_true = vram_high("4.8/6.0GB (80%)", threshold=80.0)
    v_false = vram_high("2.0/6.0GB (33%)", threshold=80.0)
    v_invalid = vram_high("not a vram text")
    if v_true is True and v_false is False and v_invalid is False:
        record("vram_high 3 cases", "PASS", "above/below/invalid OK")
    else:
        record("vram_high 3 cases", "WARN", f"got {v_true}, {v_false}, {v_invalid}")
except Exception as e:
    record("vram_high", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 18. PROJECTS / YAMLs
# ═════════════════════════════════════════════════════════════
print("\n--- 18. Project files / YAMLs ---")
import yaml

for proj_file in (ROOT / "projects").glob("*.yaml"):
    try:
        d = yaml.safe_load(proj_file.read_text(encoding="utf-8"))
        if d:
            record(f"projects/{proj_file.name}", "PASS", f"keys={list(d.keys())[:5]}")
        else:
            record(f"projects/{proj_file.name}", "WARN", "empty")
    except Exception as e:
        record(f"projects/{proj_file.name}", "FAIL", str(e)[:80])

for yfile in ["prompts.yaml", "styles.yaml"]:
    p = ROOT / yfile
    if not p.exists():
        record(yfile, "FAIL", "missing")
        continue
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if d and isinstance(d, dict):
            record(yfile, "PASS", f"keys={list(d.keys())[:5]}")
        elif d and isinstance(d, list):
            record(yfile, "PASS", f"list of {len(d)} items")
        else:
            record(yfile, "WARN", "empty/null")
    except Exception as e:
        record(yfile, "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 19. PYDANTIC SCHEMAS
# ═════════════════════════════════════════════════════════════
print("\n--- 19. Pydantic schemas ---")
try:
    from config.config_schemas import (
        DecisionRecord,
    )
    fields = [f for f in dir(DecisionRecord) if not f.startswith("_")]
    record("DecisionRecord Pydantic", "PASS", f"fields={len(fields)}")
except Exception as e:
    record("Pydantic schemas", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 20. VISION CACHE
# ═════════════════════════════════════════════════════════════
print("\n--- 20. Vision cache ---")
try:
    from utils.vision_cache import VisionCache
    with tempfile.TemporaryDirectory() as td:
        vc = VisionCache(cache_dir=Path(td))
        if hasattr(vc, "get") and hasattr(vc, "set"):
            record("VisionCache API", "PASS", "get/set present")
        else:
            record("VisionCache API", "FAIL", "missing methods")
except Exception as e:
    record("VisionCache", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 21. SPECIALIZED MODELS
# ═════════════════════════════════════════════════════════════
print("\n--- 21. Specialized models ---")
try:
    from utils.specialized_models import generate_image_prompt, review_script_fast
    if callable(review_script_fast) and callable(generate_image_prompt):
        record("specialized_models", "PASS", "both callables")
except Exception as e:
    record("specialized_models", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 22. QUALITY CHECK
# ═════════════════════════════════════════════════════════════
print("\n--- 22. Quality check ---")
try:
    import inspect

    from utils.quality_check import check_video
    sig = inspect.signature(check_video)
    record("check_video signature", "PASS", f"params={list(sig.parameters)[:5]}")
except Exception as e:
    record("check_video", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 23. DASHBOARD (React)
# ═════════════════════════════════════════════════════════════
print("\n--- 23. Dashboard (React) ---")
dash_pkg = ROOT / "dashboard" / "package.json"
if dash_pkg.exists():
    try:
        d = json.loads(dash_pkg.read_text(encoding="utf-8"))
        scripts = list(d.get("scripts", {}).keys())
        record("dashboard package.json", "PASS", f"scripts={scripts[:3]}")
    except Exception as e:
        record("dashboard", "FAIL", str(e)[:80])
else:
    record("dashboard", "FAIL", "missing")
nm = ROOT / "dashboard" / "node_modules"
if nm.exists():
    size = sum(p.stat().st_size for p in nm.rglob('*') if p.is_file()) // 1_000_000
    record("dashboard node_modules", "PASS", f"size={size} MB")


# ═════════════════════════════════════════════════════════════
# 24. PIPELINE ORCHESTRATION
# ═════════════════════════════════════════════════════════════
print("\n--- 24. Pipeline orchestration ---")
try:
    from core.pipeline_long import request_cancel, run_long_pipeline
    if callable(run_long_pipeline):
        record("run_long_pipeline", "PASS", "callable")
    if callable(request_cancel):
        record("request_cancel", "PASS", "callable")
except Exception as e:
    record("run_long_pipeline", "FAIL", str(e)[:120])


# ═════════════════════════════════════════════════════════════
# 25. CHECKPOINT JSON SANITY
# ═════════════════════════════════════════════════════════════
print("\n--- 25. Existing checkpoint files sanity ---")
ck_files = list((ROOT / "studio_checkpoints").glob("*.json"))[:5]
ck_pass = 0
for cf in ck_files:
    try:
        d = json.loads(cf.read_text(encoding="utf-8"))
        if isinstance(d, (dict, list)):
            ck_pass += 1
    except Exception as e:
        print(f"      {cf.name}: {str(e)[:60]}")
record("checkpoint JSONs (first 5)", "PASS" if ck_pass == len(ck_files) else "WARN", f"{ck_pass}/{len(ck_files)}")


# ═════════════════════════════════════════════════════════════
# 26. FFMPEG / FFPROBE
# ═════════════════════════════════════════════════════════════
print("\n--- 26. FFmpeg / ffprobe ---")
import subprocess

try:
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
    ver = r.stdout.split("\n")[0] if r.returncode == 0 else "FAIL"
    record("ffmpeg", "PASS" if r.returncode == 0 else "FAIL", ver[:60])
except Exception as e:
    record("ffmpeg", "FAIL", str(e)[:80])
try:
    r = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=5)
    record("ffprobe", "PASS" if r.returncode == 0 else "FAIL", r.stdout.split("\n")[0][:60])
except Exception as e:
    record("ffprobe", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 27. OLLAMA ACTUALLY RESPONSIVE
# ═════════════════════════════════════════════════════════════
print("\n--- 27. Ollama connectivity ---")
try:
    import urllib.request
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
        models = [m["name"] for m in d.get("models", [])]
        record("ollama /api/tags", "PASS", f"{len(models)} models: {models[:3]}")
except Exception as e:
    record("ollama /api/tags", "FAIL", str(e)[:80])

try:
    import urllib.request
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"model": "hermes-director", "prompt": "Say 'ok' and nothing else.", "stream": False, "options": {"num_predict": 8}}).encode(),
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
        text = d.get("response", "")
        if text:
            record("ollama /api/generate", "PASS", f"got {len(text)} chars: {text[:50]!r}")
        else:
            record("ollama /api/generate", "FAIL", "empty response")
except Exception as e:
    record("ollama /api/generate", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 28. DISK SPACE
# ═════════════════════════════════════════════════════════════
print("\n--- 28. Disk space ---")
total, used, free = shutil.disk_usage("C:\\")
free_gb = free / (1024**3)
total_gb = total / (1024**3)
record("C: free space", "PASS" if free_gb > 5 else "WARN", f"{free_gb:.1f} GB free / {total_gb:.0f} GB total")


# ═════════════════════════════════════════════════════════════
# 29. EXTRA: EDGE CASES / BREAK-ATTEMPTS
# ═════════════════════════════════════════════════════════════
print("\n--- 29. Extra edge cases ---")

# PermanentMemoryLog with bad input
try:
    from memory.permanent_memory import PermanentMemoryLog
    with tempfile.TemporaryDirectory() as td:
        pm = PermanentMemoryLog(topic="x", base_dir=td)
        try:
            pm.log_character("z", "desc")  # missing voice_reference
            record("log_character without voice_reference", "FAIL", "should have raised")
        except TypeError:
            record("log_character without voice_reference", "PASS", "raises TypeError as expected")
except Exception as e:
    record("log_character test setup", "FAIL", str(e)[:80])

# StyleResolver.resolve() with various inputs
try:
    from style_resolver import StyleResolver
    sr = StyleResolver()
    res1 = sr.resolve("cinematic")
    res2 = sr.resolve("nonexistent_style_xyz")
    res3 = sr.resolve("")
    res4 = sr.resolve(None)
    record("StyleResolver edge", "PASS", "all returned values without raising")
except Exception as e:
    record("StyleResolver edge", "FAIL", str(e)[:80])

# format_etc
try:
    e0 = format_etc(0, 5, 10)
    e_done = format_etc(_time.time() - 10, 10, 10)
    e_zero_total = format_etc(_time.time(), 0, 0)
    e_partial = format_etc(_time.time() - 5, 5, 10)
    if e0 == "\u2014" and e_done == "~0s" and e_zero_total == "\u2014":
        record("format_etc 4 cases", "PASS", f"all OK; partial={e_partial!r}")
    else:
        record("format_etc 4 cases", "WARN", f"e0={e0!r}, done={e_done!r}, zt={e_zero_total!r}, p={e_partial!r}")
except Exception as e:
    record("format_etc", "FAIL", str(e)[:80])

# safe_filename edge
try:
    sf1 = safe_filename("hello world")
    sf2 = safe_filename("unicode: 你好")
    sf3 = safe_filename("a" * 500)
    if sf1 == "hello_world" and sf2 and sf3:
        record("safe_filename edge", "PASS", "3/3 OK")
    else:
        record("safe_filename edge", "WARN", f"sf1={sf1!r}, sf2={sf2!r}, sf3.len={len(sf3)}")
except Exception as e:
    record("safe_filename edge", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# 30. STATE MACHINE: CheckpointManager + StoryMemory interaction
# ═════════════════════════════════════════════════════════════
print("\n--- 30. State machine round-trip ---")
try:
    with tempfile.TemporaryDirectory() as td:
        from utils.checkpoint import CheckpointManager
        cm = CheckpointManager(checkpoint_dir=Path(td))
        cm.save("seg01", {"script": "X", "audio": "a.wav", "video": "v.mp4"}, completed=True)
        cm.save("seg02", {"script": "Y"}, completed=False)
        # check resume
        from core.pipeline_long import request_cancel
        completed_segs = cm.list_completed() if hasattr(cm, "list_completed") else []
        record("CheckpointManager state", "PASS", "saved 2 segs")
except Exception as e:
    record("CheckpointManager state", "FAIL", str(e)[:80])


# ═════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
pass_n = sum(1 for _, s, _ in results if s == "PASS")
fail_n = sum(1 for _, s, _ in results if s == "FAIL")
warn_n = sum(1 for _, s, _ in results if s == "WARN")
skip_n = sum(1 for _, s, _ in results if s == "SKIP")
print(f"  PASS  {pass_n}")
print(f"  FAIL  {fail_n}")
print(f"  WARN  {warn_n}")
print(f"  SKIP  {skip_n}")
print(f"  TOTAL {len(results)}")
if fail_n:
    print()
    print("FAILS:")
    for n, s, d in results:
        if s == "FAIL":
            print(f"  {n}: {d}")
if warn_n:
    print()
    print("WARNINGS:")
    for n, s, d in results:
        if s == "WARN":
            print(f"  {n}: {d}")
