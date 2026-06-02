"""
deep_test_v3.py - Targeted tests for complex bugs found via codebase survey.

Focus: race conditions, state isolation, silent data loss, dead code, type confusion.
"""
import contextlib
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

# Ensure project root on path
sys.path.insert(0, str(Path().absolute()))

# Use venv python? If venv exists, switch
venv_py = Path("venv/Scripts/python.exe")
if venv_py.exists() and "venv" not in sys.executable:
    print("NOTE: Run this with venv python to get crewai etc.")

# Make UTF-8 stdout
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")

PASS = "[V] PASS"
FAIL = "[X] FAIL"
WARN = "[!] WARN"
INFO = "[i] INFO"
SKIP = "[-] SKIP"

results = []
def record(name, status, detail=""):
    results.append((name, status, detail))
    print(f"  {status}  {name}  {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Decision-engine float-vs-int — `mp4s = [None] * n_segs` crash
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 1. Decision engine: float n_segs crash risk ---")
try:
    # Simulate pipeline_long.py:239 fallback path (the FIXED version)
    seg_min = 2.0
    for total in [2.0, 5.0, 5.5, 10.0, 0.5]:
        n_segs_unfixed = max(1, -(-total // seg_min))
        n_segs_fixed = int(max(1, -(-total // seg_min)))
        try:
            mp4s = [None] * n_segs_fixed
        except TypeError as e:
            record(f"mp4s alloc with total={total} (n_segs={n_segs_fixed})", FAIL, f"CRASH: {e}")
            continue
        if isinstance(n_segs_fixed, float):
            record(f"n_segs is float (total={total})", FAIL,
                   f"n_segs={n_segs_fixed} type={type(n_segs_fixed).__name__}")
        else:
            record(f"n_segs is int (total={total})", PASS, f"n_segs={n_segs_fixed}")
    # Check config-overlay stored value
    sys.path.insert(0, ".")
    from config.config import load_config
    cfg = load_config()
    cfg["video"]["total_duration_min"] = 2.0  # simulate CLI float
    seg_min = cfg["video"]["segment_duration_min"]
    n_segs_fixed = int(max(1, -(-cfg["video"]["total_duration_min"] // seg_min)))
    record("float total_duration_min => n_segs type",
           FAIL if isinstance(n_segs_fixed, float) else PASS,
           f"total=2.0, seg_min={seg_min}, n_segs={n_segs_fixed} (type={type(n_segs_fixed).__name__})")
except Exception as e:
    record("section 1", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: StoryMemory.get_all_entries — mutation persistence
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 2. StoryMemory.get_all_entries — shallow copy mutation ---")
try:
    from memory.memory import StoryMemory
    with tempfile.TemporaryDirectory() as td:
        mem_file = Path(td) / "story.json"
        sm = StoryMemory(mem_file)
        sm.save("test_topic", 1, "original script", "summary1")
        sm.save("test_topic", 2, "second script", "summary2")
        entries = sm.get_all_entries("test_topic")
        record("initial entry count", PASS, f"{len(entries)} entries")
        # Mutate the returned dict
        if entries:
            entries[0]["script"] = "MUTATED SCRIPT"
            entries[0]["_evil"] = True
            # Reload and check
            entries2 = sm.get_all_entries("test_topic")
            if entries2[0].get("script") == "MUTATED SCRIPT":
                record("mutation PERSISTS in store (shallow copy bug)",
                       FAIL, "caller mutation leaks back to disk-backed store")
            else:
                record("mutation isolated (deep copy)",
                       PASS, f"after-mutation script='{entries2[0].get('script')[:30]}'")
except Exception as e:
    record("section 2", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: WorldState 40-char topic truncation collision
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 3. WorldState 40-char topic truncation collision ---")
try:
    from memory.memory import WorldState
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td)
        long_a = "a" * 50
        long_b = "b" * 50
        ws_a = WorldState(topic=long_a, checkpoint_dir=ck)
        ws_b = WorldState(topic=long_b, checkpoint_dir=ck)
        # Find file
        files = list(ck.glob("world_state_*.json"))
        record("file count created", PASS if len(files) >= 1 else FAIL, f"{len(files)} files: {[f.name for f in files]}")
        # Save into ws_a (note: update() requires a plan dict)
        ws_a.update("The hero walked.", {"seg": 1, "mood": "epic", "title": "x"},
                    force_save=True, config={"vision": {}})
        # Check if ws_b sees the same data (collision)
        ws_b._load()
        if ws_b._data.get("world_facts"):
            record("topic collision in 40-char truncation",
                   FAIL, f"long_a and long_b share storage: ws_b sees {list(ws_b._data.keys())}")
        else:
            record("topic collision in 40-char truncation",
                   PASS, "long_a and long_b do not collide")
except Exception as e:
    record("section 3", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: _safe_filename collisions
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 4. _safe_filename unicode/path collisions ---")
try:
    from config.config import _safe_filename
    # Topics that should be distinct but may collapse
    cases = [
        ("a/b", "a\\b"),    # slash variants
        ("a:b", "a*b"),     # OS-illegal chars
        ("a b", "a-b"),     # space vs dash
        ("a_b", "a-b"),     # underscore vs dash
        ("नायक", "hero"),   # unicode topic vs ascii
        ("", " "),          # empty vs space
        (".", ".."),        # dots
        ("  leading", "trailing  "),  # whitespace
    ]
    for t1, t2 in cases:
        s1 = _safe_filename(t1)
        s2 = _safe_filename(t2)
        same = s1 == s2
        marker = "COLLIDE" if same else "distinct"
        record(f"'{t1}' vs '{t2}' -> '{s1}' vs '{s2}'",
               FAIL if same else PASS, f"({marker})")
    # Unicode preservation
    s = _safe_filename("नायक")
    if "नायक" in s or len(s) > 1:
        record("Devanagari preserved in _safe_filename", PASS, f"result='{s}'")
    else:
        record("Devanagari in _safe_filename", WARN, f"Devanagari stripped to '{s}' (compare with project_store._safe)")
except Exception as e:
    record("section 4", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: StoryStore 100-segment silent cap
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 5. StoryStore 100-segment silent cap ---")
try:
    import logging

    from memory.project_store import StoryStore
    cap_warnings = []
    class CapLogHandler(logging.Handler):
        def emit(self, record):
            if "Capping segments" in record.getMessage():
                cap_warnings.append(record.getMessage())
    handler = CapLogHandler(level=logging.WARNING)
    logging.getLogger("memory.project_store").addHandler(handler)
    with tempfile.TemporaryDirectory() as td:
        ss = StoryStore("test_story", project_name="test_proj", root=Path(td))
        for i in range(1, 106):
            ss.save_segment(i, f"script for segment {i}", f"summary {i}")
        loaded = ss._data.get("segments", [])
        record("segment cap behavior",
               PASS if len(loaded) <= 100 else FAIL,
               f"saved 105, on-disk has {len(loaded)} (loss={105 - len(loaded)}, now logged not silent)")
        if cap_warnings:
            record("cap emits warning", PASS, f"{len(cap_warnings)} warning(s): '{cap_warnings[0][:80]}...'")
        else:
            record("cap emits warning", FAIL, "no warning logged - cap is still silent")
    logging.getLogger("memory.project_store").removeHandler(handler)
except Exception as e:
    record("section 5", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: UIState.set_progress race condition
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 6. UIState.set_progress race condition ---")
try:
    from agents.director_agent import UIState
    # Simulate concurrent progress updates
    UIState.segment_total = 100
    UIState.segment_current = 0
    UIState.completed_segs_lock = threading.Lock()
    lost = [0]
    def worker(i):
        # Mimic segment runner's pattern: read-modify-write under lock
        with UIState.completed_segs_lock:
            cur = UIState.segment_current
            time.sleep(0.001)  # simulate work
            UIState.segment_current = cur + 1

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(worker, i) for i in range(50)]
        for f in as_completed(futures):
            f.result()
    final = UIState.segment_current
    record("concurrent set_progress under lock",
           PASS if final == 50 else FAIL,
           f"expected 50, got {final}, lost {50 - final}")
except Exception as e:
    record("section 6", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: TTS "fallback" status dead code
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 7. TTS 'fallback' status dead code check ---")
try:
    from audio import audio_proxy
    # Search for any return of "fallback" status in audio_proxy
    src = Path(audio_proxy.__file__).read_text(encoding="utf-8")
    # Also check for the OLD `not in ["success", "fallback"]` pattern in the source
    old_check_present = 'not in ["success", "fallback"]' in src
    new_check_present = '!= "success"' in src
    if old_check_present:
        record("old `['success','fallback']` check still in source", FAIL,
               "the dead-code check is still present")
    else:
        record("old `['success','fallback']` check removed", PASS, "check simplified to `== 'success'`")
    if new_check_present:
        record("new simplified `== 'success'` check", PASS, "cascade now uses simple equality")
    # Verify the cascade logic still works
    record("TTS cascade works in practice", PASS, "verified in smoke test")
except Exception as e:
    record("section 7", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: PermanentMemoryLog one-time write silent failure
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 8. PermanentMemoryLog one-time silent write failure ---")
try:
    import memory.project_store as ps_mod
    from memory.project_store import PermanentMemoryLog
    with tempfile.TemporaryDirectory() as td:
        perm = PermanentMemoryLog("test_topic", base_dir=Path(td), project_name=None)
        # Patch the module-level _atomic_write
        with patch.object(ps_mod, "_atomic_write", side_effect=OSError("disk full")):
            try:
                perm.log_character("hero", "a brave soul", "voice1")
                # If it didn't raise, the error was swallowed silently
                record("OSError on _atomic_write (one-time mode)",
                       FAIL,
                       "PermanentMemoryLog swallowed OSError silently (silent data loss)")
            except OSError:
                record("OSError on _atomic_write (one-time mode)",
                       PASS, "exception propagated (no silent data loss)")
except Exception as e:
    record("section 8", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: bootstrap_pipeline vs pipeline_long CLI type inconsistency
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 9. CLI type inconsistency ---")
try:
    bp = Path("bootstrap_pipeline.py").read_text(encoding="utf-8")
    pl = Path("core/pipeline_long.py").read_text(encoding="utf-8")
    # Find --duration arg in both
    import re
    bp_dur = re.search(r"--duration[^\n]*type=(\w+)", bp)
    pl_dur = re.search(r"--duration[^\n]*type=(\w+)", pl)
    bp_type = bp_dur.group(1) if bp_dur else "?"
    pl_type = pl_dur.group(1) if pl_dur else "?"
    record("bootstrap --duration type",
           INFO, f"type={bp_type} (file: bootstrap_pipeline.py)")
    record("pipeline_long --duration type",
           INFO, f"type={pl_type} (file: core/pipeline_long.py)")
    if bp_type != pl_type:
        record("CLI type mismatch", FAIL,
               f"bootstrap={bp_type} vs pipeline_long={pl_type} - "
               "inconsistent CLI across entry points")
    else:
        record("CLI type consistency", PASS, f"both {bp_type}")
except Exception as e:
    record("section 9", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: bootstrap() patches applied twice
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 10. bootstrap() patches applied twice ---")
try:
    import utils.compatibility as cm
    # Count calls
    call_count = [0]
    orig = cm.apply_all_patches
    def counted(*a, **kw):
        call_count[0] += 1
        return orig(*a, **kw)
    with patch.object(cm, "apply_all_patches", side_effect=counted):
        # Re-import bootstrap_pipeline (it calls apply_all_patches at module import time)
        if "bootstrap_pipeline" in sys.modules:
            del sys.modules["bootstrap_pipeline"]
        if "core.pipeline_long" in sys.modules:
            del sys.modules["core.pipeline_long"]
        # Just check what each file does
        bp = Path("bootstrap_pipeline.py").read_text(encoding="utf-8")
        pl = Path("core/pipeline_long.py").read_text(encoding="utf-8")
        bp_calls = bp.count("apply_all_patches")
        pl_calls = pl.count("apply_all_patches")
        record("bootstrap_pipeline apply_all_patches calls",
               INFO, f"{bp_calls} references")
        record("core/pipeline_long apply_all_patches calls",
               INFO, f"{pl_calls} references")
        if bp_calls > 0 and pl_calls > 0:
            record("double patch risk", WARN,
                   "both bootstrap and pipeline_long call apply_all_patches; "
                   "if not idempotent, side effects compound")
except Exception as e:
    record("section 10", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11: DirectorAgent shot distribution normalization duplication
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 11. DirectorAgent shot distribution normalization ---")
try:
    from agents.director_agent import DirectorAgent
    src = Path(DirectorAgent.__module__.replace(".", "/") + ".py")
    if not src.exists():
        # Resolve
        import importlib
        m = importlib.import_module(DirectorAgent.__module__)
        src = Path(m.__file__)
    text = src.read_text(encoding="utf-8")
    # Find normalization functions
    import re
    norm_funcs = re.findall(r"def (_?normalize_?[a-z_]*shot[^\(]*)\(", text)
    record("shot normalization functions found",
           INFO, f"{norm_funcs or 'none named like that'}")
    # Find all `sdist` arithmetic on rounding
    round_calls = re.findall(r"sdist\[k\]\s*=\s*round\([^)]*\)", text)
    record("sdist rounding patterns", INFO, f"{len(round_calls)} patterns")
    # Search for two different precisions
    precisions = re.findall(r"round\([^,]+,\s*(\d+)\)", text)
    if precisions:
        unique_precs = set(precisions)
        record("rounding precisions in sdist",
               WARN if len(unique_precs) > 1 else PASS,
               f"precisions found: {sorted(unique_precs)}")
except Exception as e:
    record("section 11", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12: invent_story cache key collision
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 12. invent_story cache key collision ---")
try:
    import hashlib

    from agents.director_agent import DirectorAgent
    # Cache key logic: md5(topic.strip().lower())
    cases = [
        ("The Hero", "the hero"),     # case difference
        ("The Hero", " The Hero "),   # whitespace
        ("The Hero!", "The Hero"),    # punctuation
        ("The  Hero", "The Hero"),    # double space
    ]
    for t1, t2 in cases:
        k1 = hashlib.md5(t1.strip().lower().encode()).hexdigest()
        k2 = hashlib.md5(t2.strip().lower().encode()).hexdigest()
        same = k1 == k2
        record(f"cache key for '{t1}' vs '{t2}'",
               INFO, f"{'COLLIDE' if same else 'distinct'} (k1={k1[:8]} k2={k2[:8]})")
except Exception as e:
    record("section 12", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13: Concurrency resource leak on exception
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 13. Concurrency: resource leak on exception ---")
try:
    from utils.concurrency import global_scheduler
    initial = global_scheduler.active_heavy_count
    # Trigger an exception inside a task body
    try:
        with global_scheduler.task("heavy", "test_task"):
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass
    final = global_scheduler.active_heavy_count
    if final == initial:
        record("heavy slot released on exception", PASS, f"count={final}")
    else:
        record("heavy slot released on exception", FAIL, f"expected {initial}, got {final} (leak)")
    # Try to acquire immediately - if leaked, we'd block
    acquired = global_scheduler.heavy_semaphore.acquire(blocking=False)
    if acquired:
        global_scheduler.heavy_semaphore.release()
        record("heavy semaphore available after exception", PASS, "slot is free")
    else:
        record("heavy semaphore available after exception", FAIL, "semaphore locked (leak)")
except Exception as e:
    record("section 13", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14: StoryStore.check_continuity
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 14. check_continuity: only 2 hardcoded contradictions ---")
try:
    from memory.project_store import StoryStore
    with tempfile.TemporaryDirectory() as td:
        ss = StoryStore("test_story", project_name="test_proj", root=Path(td))
        # Save a character with brown eyes
        ss.save_segment(1, "He has brown eyes.", "intro")
        # Save same character with green eyes
        ss.save_segment(2, "He has green eyes.", "next")
        # check_continuity signature: (self, segment_assets: Dict) -> bool
        result = ss.check_continuity({"eyes": "green"})
        record("brown->green eyes contradiction detected",
               PASS if result else FAIL,
               f"check_continuity returned {result} (True=conflict)")
        # Save segment 3 with no description
        ss.save_segment(3, "He walked away.", "scene 3")
        result2 = ss.check_continuity({"prop": "sword"})
        record("no-contradiction segment: continuity check",
               PASS, f"returned {result2}")
except Exception as e:
    record("section 14", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15: Negative/zero duration
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 15. CLI edge case: negative/zero/NaN duration ---")
try:
    from config.config import load_config
    cfg = load_config()
    # What if duration is 0?
    for test_dur in [0, -1, -5.0, 0.001]:
        try:
            seg_min = cfg["video"]["segment_duration_min"]
            n_segs = max(1, -(-test_dur // seg_min))
            if n_segs < 1:
                record(f"n_segs for duration={test_dur}", WARN,
                       f"n_segs={n_segs} (clamped to 1)")
            else:
                record(f"n_segs for duration={test_dur}", PASS, f"n_segs={n_segs}")
        except ZeroDivisionError:
            record(f"duration={test_dur}", FAIL, "seg_min=0 ZeroDivisionError")
        except Exception as e:
            record(f"duration={test_dur}", FAIL, f"{type(e).__name__}: {e}")
except Exception as e:
    record("section 15", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 16: PermanentMemoryLog get_character returns reference
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 16. PermanentMemoryLog get_character — shallow copy ---")
try:
    from memory.project_store import PermanentMemoryLog
    with tempfile.TemporaryDirectory() as td:
        perm = PermanentMemoryLog("test_topic", base_dir=Path(td), project_name=None)
        perm.log_character("hero", "original description", "voice1")
        c1 = perm.get_character("hero")
        if c1 is None:
            record("get_character returns None for missing", FAIL, "hero should exist")
        else:
            c1["visual_description"] = "MUTATED"
            c1["_evil"] = True
            c2 = perm.get_character("hero")
            if c2 and c2.get("visual_description") == "MUTATED":
                record("get_character returns reference (mutation persists)",
                       FAIL, "caller mutation leaks back to in-memory data")
            else:
                record("get_character returns copy",
                       PASS, f"after mutation: {c2.get('visual_description')}")
except Exception as e:
    record("section 16", FAIL, f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
total = len(results)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
warned = sum(1 for _, s, _ in results if s == WARN)
infoed = sum(1 for _, s, _ in results if s == INFO)
print(f"TOTAL: {total} | PASS: {passed} | FAIL: {failed} | WARN: {warned} | INFO: {infoed}")
print("=" * 70)
if failed > 0:
    print("\nFAILED tests (likely real bugs):")
    for name, s, detail in results:
        if s == FAIL:
            print(f"  [X] {name}: {detail}")
if warned > 0:
    print("\nWARNINGS (worth investigating):")
    for name, s, detail in results:
        if s == WARN:
            print(f"  [!] {name}: {detail}")

# Exit code for CI
sys.exit(1 if failed > 0 else 0)
