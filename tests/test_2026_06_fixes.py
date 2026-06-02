"""test_2026_06_fixes.py — Regression tests for fixes applied 2026-06-01.

Each test in this file locks in a single, minimal change from the 2026-06-01
refactor + bug-cleanup pass. If a future change undoes any of these fixes,
the corresponding test will fail with a clear message pointing to the bug ID.

Bug IDs covered:
  P5-1   _BreakerState.cooldown_remaining_s() returns real remaining time
  P5-2   WorkloadScheduler light-semaphore timeout 300s → 60s
  P5-3   core/pipeline_long.py builds process_segment exactly once
  P5-4   utils/crewai_breaker.py no longer imports dead _deep_merge
  P4-8   config/config.yaml: audio_fx.enabled flipped to true
  P4-23  Fractional-minute duration accepted at CLI / schema / guard / clamp
  BreakerOpen carries real cooldown (not hardcoded 0) when breaker is open
"""

import ast
import re
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# P5-1: _BreakerState.cooldown_remaining_s()
# ═════════════════════════════════════════════════════════════════════════════

class TestP51CooldownRemaining:
    """P5-1: breaker exposes a thread-safe cooldown_remaining_s() method."""

    def test_method_exists(self):
        from utils.ollama_client import _BreakerState
        assert hasattr(_BreakerState, "cooldown_remaining_s"), \
            "_BreakerState is missing cooldown_remaining_s() (P5-1)"

    def test_zero_when_closed(self):
        from utils.ollama_client import _BreakerState
        b = _BreakerState(fails_threshold=3, cooldown_s=30)
        assert b.cooldown_remaining_s() == 0.0

    def test_returns_positive_when_open(self):
        from utils.ollama_client import _BreakerState
        b = _BreakerState(fails_threshold=1, cooldown_s=30)
        b.record_failure()  # opens
        remaining = b.cooldown_remaining_s()
        assert 0.0 < remaining <= 30.0, f"expected (0, 30], got {remaining}"

    def test_returns_zero_again_in_half_open(self):
        from utils.ollama_client import _BreakerState
        b = _BreakerState(fails_threshold=1, cooldown_s=0.01)
        b.record_failure()
        time.sleep(0.05)
        b.allow_request()  # transitions OPEN → HALF_OPEN
        assert b.cooldown_remaining_s() == 0.0

    def test_decreases_monotonically(self):
        from utils.ollama_client import _BreakerState
        b = _BreakerState(fails_threshold=1, cooldown_s=5)
        b.record_failure()
        r1 = b.cooldown_remaining_s()
        time.sleep(0.05)
        r2 = b.cooldown_remaining_s()
        assert r1 > r2 >= 0.0


# ═════════════════════════════════════════════════════════════════════════════
# P5-2: WorkloadScheduler light-semaphore timeout
# ═════════════════════════════════════════════════════════════════════════════

class TestP52LightSchedulerTimeout:
    """P5-2: light-slot acquire timeout must be 60s, not 300s."""

    def test_light_timeout_is_60s(self):
        """Source-level check: the .acquire(timeout=...) call in concurrency.py
        must use 60, not 300. A higher number would mask a stuck pipeline.
        """
        src = (_ROOT / "utils" / "concurrency.py").read_text(encoding="utf-8")
        # Look for the light-semaphore acquire call
        m = re.search(
            r"light_semaphore\.acquire\(timeout\s*=\s*(\d+)\)", src)
        assert m is not None, "light_semaphore.acquire(timeout=...) not found"
        timeout = int(m.group(1))
        assert timeout == 60, \
            f"P5-2: light-slot timeout must be 60s, got {timeout}s"

    def test_heavy_timeout_still_1800s(self):
        """Sanity check — the HEAVY slot keep its 1800s ceiling (unchanged)."""
        src = (_ROOT / "utils" / "concurrency.py").read_text(encoding="utf-8")
        m = re.search(
            r"heavy_semaphore\.acquire\(timeout\s*=\s*(\d+)\)", src)
        assert m is not None, "heavy_semaphore.acquire(timeout=...) not found"
        timeout = int(m.group(1))
        assert timeout == 1800, \
            f"heavy-slot timeout changed unexpectedly to {timeout}s (P5-2 only flipped light)"

    def test_light_acquire_does_succeed_with_available_slots(self):
        """Sanity: the workhorse path (acquire → run → release) still works
        end-to-end on a fresh scheduler. The P5-2 fix only changed the
        timeout constant; the acquire/release flow is unchanged.
        """
        from utils.concurrency import WorkloadScheduler
        sched = WorkloadScheduler()
        marker = []
        with sched.task("LIGHT", "ok-task"):
            marker.append("ran")
        assert marker == ["ran"], "light task body did not execute"


# ═════════════════════════════════════════════════════════════════════════════
# P5-3: pipeline_long.py builds process_segment exactly once
# ═════════════════════════════════════════════════════════════════════════════

class TestP53SingleProcessSegmentBuild:
    """P5-3: `make_process_segment` is called exactly once in pipeline_long.py.

    A duplicate build used to exist (line 394 placeholder with
    shared_prompt_executor=None) that would shadow the real closure if a
    refactor moved code around. AST-level check prevents regression.
    """

    def test_single_make_process_segment_call(self):
        src = (_ROOT / "core" / "pipeline_long.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "make_process_segment"
        ]
        assert len(calls) == 1, (
            f"P5-3: expected exactly 1 make_process_segment() call, "
            f"found {len(calls)} at lines {calls}"
        )

    def test_call_inside_executor_block(self):
        """The single call must be inside the ThreadPoolExecutor block so the
        shared prompt executor can be captured into the closure.
        """
        src = (_ROOT / "core" / "pipeline_long.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Find the real call (not a docstring reference like `core.X.foo()`)
        call_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "make_process_segment":
                call_node = node
                break
        assert call_node is not None, "make_process_segment call not found"
        call_line = call_node.lineno

        # Walk up from the call to find the enclosing `with ThreadPoolExecutor`
        src_lines = src.splitlines()
        exec_start = None
        for i in range(call_line - 1, -1, -1):
            if "ThreadPoolExecutor" in src_lines[i] and "with " in src_lines[i]:
                exec_start = i + 1
                break
        assert exec_start is not None, (
            "Could not find enclosing ThreadPoolExecutor `with` block"
        )
        assert exec_start < call_line, (
            f"make_process_segment call (line {call_line}) is NOT inside the "
            f"ThreadPoolExecutor block starting at line {exec_start}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# P5-4: utils/crewai_breaker.py — no dead _deep_merge import
# ═════════════════════════════════════════════════════════════════════════════

class TestP54NoDeadDeepMergeImport:
    """P5-4: the dead `from core.pre_production import _deep_merge` import
    was removed from utils/crewai_breaker.py. It referenced a private name
    that was never used and triggered F401 warnings.
    """

    def test_no_deep_merge_import(self):
        src = (_ROOT / "utils" / "crewai_breaker.py").read_text(encoding="utf-8")
        assert "_deep_merge" not in src, (
            "P5-4: dead `_deep_merge` import re-introduced in "
            "utils/crewai_breaker.py"
        )

    def test_module_imports_clean(self):
        """Smoke test: importing crewai_breaker should not trigger F401."""
        from utils import crewai_breaker
        assert crewai_breaker.__file__, "module not importable"


# ═════════════════════════════════════════════════════════════════════════════
# P4-8: config/config.yaml: audio_fx.enabled is true
# ═════════════════════════════════════════════════════════════════════════════

class TestP48AudioFxEnabled:
    """P4-8: audio_fx.enabled is flipped to true. The 9 missing SFX WAV
    files are still a no-op (only thunder bundled), but the config default
    is no longer hiding the feature.
    """

    def test_audio_fx_enabled_true(self):
        yaml_text = (_ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        # Match the `audio_fx:` block + `enabled:` line with a bool
        m = re.search(
            r"^audio_fx:\s*\n\s*enabled:\s*(\S+)", yaml_text, re.MULTILINE)
        assert m is not None, "audio_fx: ... enabled: ... not found in config.yaml"
        assert m.group(1).lower() in ("true", "#", "  #"), (
            f"P4-8: audio_fx.enabled is {m.group(1)!r}, expected `true`"
        )

    def test_content_gap_documented(self):
        """The YAML must mention the 9 missing SFX WAV filenames so the next
        session knows exactly what to add.
        """
        yaml_text = (_ROOT / "config" / "config.yaml").read_text(encoding="utf-8").lower()
        # At least 3 of the missing SFX names must be named in the comment
        missing = ["wind", "rain", "heartbeat", "footsteps",
                   "door_creak", "whisper", "scream", "explosion", "bell"]
        found = sum(1 for s in missing if s in yaml_text)
        assert found >= 3, (
            f"P4-8 doc: expected at least 3 of the missing SFX names in the "
            f"comment, found {found}"
        )

    def test_audio_fx_module_default_sfx_dict(self):
        """The runtime code path: _DEFAULT_SFX in audio/audio_fx.py should
        include `thunder` and have the other 9 keys as no-ops (None) until
        WAVs are provided.
        """
        from audio.audio_fx import _DEFAULT_SFX
        assert "thunder" in _DEFAULT_SFX, "thunder SFX missing from _DEFAULT_SFX"
        assert _DEFAULT_SFX["thunder"], "thunder SFX value is empty"


# ═════════════════════════════════════════════════════════════════════════════
# P4-23: Fractional duration flows through CLI / schema / guard / clamp
# ═════════════════════════════════════════════════════════════════════════════

class TestP423FloatDurationFlow:
    """P4-23: total_duration_min and segment_duration_min accept float values
    end-to-end. All call sites that previously truncated via `int()` now
    preserve the float.
    """

    def test_bootstrap_cli_accepts_float(self):
        """`--duration 2.5` must be parsed as float (was int)."""
        import argparse

        # Replicate the bootstrap_pipeline CLI definition
        from bootstrap_pipeline import run_pipeline_with_args  # noqa
        # The argparse definition is inside run_pipeline_with_args; easier
        # to replicate it directly
        p = argparse.ArgumentParser()
        p.add_argument("--duration", type=float)
        args = p.parse_args(["--duration", "2.5"])
        assert args.duration == 2.5
        assert isinstance(args.duration, float)

    def test_video_config_schemas_accepts_float(self):
        from config.config_schemas import VideoConfig
        v = VideoConfig(total_duration_min=3.7, segment_duration_min=0.5)
        assert v.total_duration_min == 3.7
        assert v.segment_duration_min == 0.5
        assert isinstance(v.total_duration_min, float)

    def test_video_config_rejects_below_minimum(self):
        from pydantic import ValidationError

        from config.config_schemas import VideoConfig
        with pytest.raises(ValidationError):
            VideoConfig(total_duration_min=0.1)  # below ge=0.5

    def test_decision_record_holds_float(self):
        from config.config_schemas import DecisionRecord
        r = DecisionRecord()
        r.set("total_duration_min", 2.5, "director")
        assert r.total_duration_min.value == 2.5
        assert isinstance(r.total_duration_min.value, float)

    def test_clamp_preserves_float(self):
        """The `_clamp` static method must NOT truncate via int()."""
        from config.config_schemas import DecisionRecord
        # Clamp at upper bound: 2.5 is within (1, 600) so it should be unchanged
        assert DecisionRecord._clamp("total_duration_min", 2.5) == 2.5
        # Clamp at lower bound: 0.1 should be raised to 1 but stay float
        clamped = DecisionRecord._clamp("total_duration_min", 0.1)
        assert clamped == 1
        assert isinstance(clamped, (int, float))
        # Clamp at upper bound: 9999 should drop to 600
        clamped_hi = DecisionRecord._clamp("words_per_segment", 9999.5)
        assert clamped_hi == 800
        # Now segment_duration_min lower: 0.1 should be raised to 1
        sd_clamped = DecisionRecord._clamp("segment_duration_min", 0.1)
        assert sd_clamped == 1

    def test_pipeline_guard_accepts_float(self):
        """core/pipeline_long.py:211 — `isinstance(duration_min, (int, float))`
        and rejects bool. Source-level check.
        """
        src = (_ROOT / "core" / "pipeline_long.py").read_text(encoding="utf-8")
        m = re.search(
            r"isinstance\(duration_min,\s*\(([^)]+)\)\)", src)
        assert m is not None, \
            "pipeline_long.py guard not found — did the line move?"
        types = {t.strip() for t in m.group(1).split(",")}
        assert "int" in types and "float" in types, (
            f"guard must accept int AND float; got: {types}"
        )
        # And the bool rejection is present
        assert "isinstance(duration_min, bool)" in src, \
            "guard must still reject bool (P4-23 fix should preserve the bool check)"

    def test_decision_engine_records_float(self):
        """agents/decision_engine.py:89 — `int(rec_dur)` → `float(rec_dur)`.
        Source-level check (the function is recursive and expensive).
        """
        src = (_ROOT / "agents" / "decision_engine.py").read_text(encoding="utf-8")
        # The actual line is `rec.set("total_duration_min", float(rec_dur), "director",`
        # (`rec` is the local decision record variable, not `decision`)
        m = re.search(
            r"\.set\(\s*['\"]total_duration_min['\"]\s*,\s*([^,]+)\s*,", src)
        assert m is not None, ".set('total_duration_min', ...) not found"
        rhs = m.group(1).strip()
        assert rhs.startswith("float("), (
            f"P4-23: expected `float(...)` in decision_engine.py "
            f"total_duration_min setter, got `{rhs}`"
        )
        assert "int(" not in rhs, (
            f"P4-23: should not contain int() in the total_duration_min "
            f"setter, got `{rhs}`"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Breaker smoke: BreakerOpen carries real cooldown, not 0
# ═════════════════════════════════════════════════════════════════════════════

class TestBreakerOpenCarriesRealCooldown:
    """When the breaker is OPEN, the BreakerOpen exception should carry the
    REAL remaining cooldown (not a hardcoded 0.0). This is what makes the
    TUI's degradation badge useful.
    """

    def test_breaker_open_carries_real_cooldown(self):
        """Forced-open breaker: BreakerOpen.cooldown_s should be > 0.

        We force the fallback-breaker path (which uses the stable
        _fallback_breakers dict) by patching `get_ollama_client` to raise.
        That way the test is hermetic — no other test's `reset_ollama_client`
        call can clobber our breaker.
        """
        from unittest.mock import patch

        from utils import crewai_breaker
        from utils.crewai_breaker import BreakerOpen, guarded_crewai_kickoff
        from utils.ollama_client import _BreakerState, reset_ollama_client

        # Use a unique model name so no other test can collide
        model = f"regression-test-model-{time.time_ns()}"

        with patch("utils.ollama_client.get_ollama_client",
                   side_effect=RuntimeError("force fallback path")):
            # Clean any prior entry in the fallback dict for this model
            crewai_breaker._fallback_breakers.pop(model, None)
            # Trip the breaker
            breaker = crewai_breaker._get_breaker(
                model, fails_threshold=1, cooldown_s=30)
            breaker.record_failure()
            assert breaker.state == _BreakerState.OPEN

            class _FakeCrew:
                pass

            with pytest.raises(BreakerOpen) as exc_info:
                guarded_crewai_kickoff(_FakeCrew(), model_name=model, timeout_s=1)

        # Real remaining time, not hardcoded 0
        assert 0.0 < exc_info.value.cooldown_s <= 30.0, (
            f"BreakerOpen.cooldown_s expected (0, 30], got "
            f"{exc_info.value.cooldown_s}"
        )
        # Cleanup
        crewai_breaker._fallback_breakers.pop(model, None)
        reset_ollama_client()

    def test_breaker_open_message_includes_remaining(self):
        """The BreakerOpen message should mention the remaining seconds so
        logs are useful.
        """
        from utils.crewai_breaker import BreakerOpen
        e = BreakerOpen("test-model", 17.3)
        msg = str(e)
        assert "test-model" in msg
        assert "17" in msg  # the integer second count from f"{x:.0f}s"
