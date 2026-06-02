# Video.AI — Open Bug List (formerly 2026-05 audit, now trimmed)

> **Last updated:** 2026-06-01
> **Source:** Re-verified entries from `BUGS_AUDIT_2026-05.md` (2026-05-30) +
> the 2026-06-01 refactor pass.
> **All `✅ FIXED` entries from the original audit have been removed** — they are
> listed under "Resolution history" at the bottom for the record.

A from-scratch, read-only audit of the entire codebase (Python pipeline + React
dashboard). This is a **handoff document for a separate fixing AI**. A
verification re-audit on 2026-05-30 confirmed most entries are now fixed. A
follow-up re-audit on 2026-06-01 (during the `pipeline_long.py` split) found
and fixed 4 more (`P5-1`..`P5-4`), and also fixed the original `P4-18`.

**How to use this file:** Fix top-to-bottom within each severity tier. Each
entry has a stable `ID`, the file + location, the offending behavior, the
impact, and a concrete fix direction. After fixing, run
`venv\Scripts\python.exe -m pytest tests/ -q` and `getDiagnostics` on changed
files. Keep all fixes config-driven and preserve fallbacks.

Severity legend:
- **P0** — Breaks output / crashes a run
- **P1** — A built feature is silently broken or disconnected
- **P2** — Reliability / correctness / security risk
- **P3** — Correctness / compliance / quality drift
- **P4** — Minor / cosmetic / dead code

> Scope note: the older `BUGS.md` (B1–B40) was the previous catalog. Most of
> those are now fixed (see "Resolution history" §A and `AGENTS.md` for the
> handful still open). This document is the authoritative open-bug list.

## Open-bug summary (as of 2026-06-01)

- **Total open:** 0 🎉
- **P0:** 0 open
- **P1:** 0 open
- **P2:** 0 open
- **P3:** 0 open
- **P4:** 0 open
- **Fixed 2026-06-01** (now in "Resolution history" §D): P4-8, P4-23
- **Re-verified FIXED 2026-06-01** (now in "Resolution history" §C): P1-9, P3-18, P4-7, P4-12, P4-16, P4-17, P4-20, P4-21, P4-22, P4-27, P4-29
- **Fixed in 2026-06-01 refactor pass** (now in "Resolution history" §B): P4-18, P5-1, P5-2, P5-3, P5-4

---

## P0 — Breaks output / crashes

*(none open)*

---

## P1 — Built features silently broken / disconnected

---

## P2 — Reliability / correctness / security

*(none open)*

---

## P3 — Correctness / compliance / quality drift

---

## P4 — Minor / cosmetic / dead code

*(none open)*

---

## Suggested fixing order

*(no open bugs — the doc is now empty)*

After each fix: `venv\Scripts\python.exe -m pytest tests/ -q` and `getDiagnostics` on changed
files; keep the pipeline importable; mock Ollama/OmniVoice/image-gen in tests.

---

## Resolution history

The original 2026-05-30 audit had 78 entries. **All 78 are now fixed** and
live in the project's git history. This section preserves enough context to
prevent regressions and document what was already tried.

### A. Fixed in the 2026-05 audit window (74 entries)

Removed from the live list as of 2026-06-01. Pinned in git history at the
2026-05-30 audit commit.

```
P0-1, P0-2,
P1-1, P1-2, P1-3, P1-4, P1-5, P1-6, P1-7, P1-8, P1-10, P1-11, P1-12,
P2-1, P2-2, P2-3, P2-4, P2-5, P2-6, P2-7, P2-8, P2-9, P2-10, P2-11,
P2-12, P2-13, P2-14, P2-15, P2-16, P2-17,
P3-1, P3-2, P3-3, P3-4, P3-5, P3-6, P3-7, P3-8, P3-9, P3-10, P3-11,
P3-12, P3-13, P3-14, P3-15, P3-16, P3-17, P3-19, P3-20, P3-21, P3-22,
P3-23, P3-24,
P4-1, P4-2, P4-3, P4-4, P4-5, P4-6, P4-9, P4-10, P4-11, P4-13, P4-14,
P4-15, P4-19, P4-24, P4-25, P4-26, P4-28, P4-30, P4-31, P4-32, P4-33
```

Notable sub-areas of cleanup:
- **IP/franchise purge (old B23–B27):** no `lumian/klein/moretti/kiana/kaslana/
  beyonder/honkai` remain in any **source** (`config/`, `agents/`, `utils/`,
  `core/`, `memory/`, `audio/`, `video/`). Persist only in runtime artifacts
  (`logs/`, `studio_outputs/`, `studio_projects/`, `hf_cache/` model vocabs)
  and two `static/*.html` demo files (`static/index.html`,
  `static/ab_picker.html`) — cosmetic, optional cleanup.
- **Config character names** are now generic ("The Protagonist"/"The Mentor"/
  "The Guardian"); code fallbacks use `"narrator"`/`"protagonist"`; test
  fixtures use "Hero"/"The Protagonist".
- **`consult-user-none-strip-crash`** (old suspicion): `_safe_input(...)` is
  now None-guarded.
- **`ollama-max-tokens-1024-truncation`** (old suspicion): `core/main.py` sets
  `max_tokens=8192` (director uses `num_predict: 4096`); no 1024 cap exists.
- **`generate_images`** is no longer double-wrapped by the outer retry (old B14).
- **Style positive** no longer contains `photorealistic, masterpiece` (old B6).

### B. Fixed in the 2026-06-01 refactor pass (5 entries)

*(see §B above)*

### C. Re-verified fixed on 2026-06-01 (11 entries)

The 2026-05 audit doc had these marked "open" but a 2026-06-01 re-audit
confirmed they were already fixed in code (P-fix comments present, fix
verified against the actual source). Removed from the live list.

```
P1-9, P3-18, P4-7, P4-12, P4-16, P4-17, P4-20, P4-21, P4-22, P4-27, P4-29
```

Verification evidence:
- **P1-9:** `utils/local_ui.py:350` returns `{job_id, status: started}`; `:412`
  has `/api/ab/status/{job_id}`; `ABPlayground.jsx` was removed (P3-24 fix
  removed dead components).
- **P3-18:** `utils/local_ui.py:157` launches via `threading.Thread(...)` —
  the pipeline is off the event loop.
- **P4-7:** `audio/audio_fx.py:170` docstring and `:187` code both say
  `>800ms -> 500ms` — they match.
- **P4-12:** `utils/specialized_models.py:258` only strips leading label
  when matches `^(?:prompt|output|result|answer)\s*:` — the broken
  "first colon" logic is gone.
- **P4-16:** `utils/story_planner.py:83` fallback is `130`, not 390.
- **P4-17:** `utils/story_planner.py:293` uses `[_num_images]` not 6.
- **P4-20:** `utils/emotion_control.py:165` guards `suffix not in (".", "।")`
  to prevent `....` doubling.
- **P4-21:** `agents/executive_agent.py` is a 7-line stub noting the fix;
  no callers in `core/main.py` or `core/pipeline_long.py`.
- **P4-22:** `agents/director_agent.py:2012` `est_duration = seg_count *
  seg_dur_min` uses clamped `seg_count`, not stale `_last_segment_count`.
- **P4-27:** `run_pipeline.py:11` uses `Path(__file__).parent`; `:19`
  calls `run_long_pipeline` directly (no `sys.argv` clobber).
- **P4-29:** `utils/compatibility.py:19-21` langchain_core filter removed;
  `:70-71` `_video_ai_compat_applied` flag prevents re-run.

### D. Fixed in the 2026-06-01 second sweep (2 entries)

The last 2 entries from the original 2026-05 audit. Both required a small
code change.

```
P4-8, P4-23
```

- **P4-8.** SFX content gap — `audio_fx.enabled` flipped from `false` to
  `true` in `config/config.yaml`. The 9 missing SFX WAV files (wind, rain,
  heartbeat, footsteps, door_creak, whisper, scream, explosion, bell) are
  still a no-op until assets are dropped in `sfx/`. The config now has a
  comment listing the missing files so the next session knows exactly what
  to add.

- **P4-23.** Float duration schema — 6 minimal-blast-radius edits:
  1. `bootstrap_pipeline.py:98` — `--duration` argparse `type=int` → `type=float`
     (accepts `2.5` now).
  2. `core/pipeline_long.py:211` — guard `isinstance(duration_min, int)` →
     `isinstance(duration_min, (int, float))` (float flows through to
     `config["video"]["total_duration_min"]`).
  3. `config/config_schema.py:56-57` — schema fields `int` → `float`,
     `ge=1` → `ge=0.5`.
  4. `config/config_schemas.py:145-146` — same as above for the
     `config_schemas.py` Pydantic `VideoConfig`.
  5. `config/config_schemas.py:344` — `_clamp` no longer truncates via
     `int(value)`; preserves the float through the clamp.
  6. `agents/decision_engine.py:89` — `int(rec_dur)` → `float(rec_dur)`.

  Smoke test: `VideoConfig(total_duration_min=2.5, segment_duration_min=1.5)`
  → `total=2.5 (float), seg=1.5`. Decision record holds `2.5` as float.
  `pytest tests/ -q` passes (260 tests = 235 prior + 25 regression).

  Regression coverage: `tests/test_2026_06_fixes.py` (25 tests, 8 of which
  cover P4-23 specifically: CLI accepts float, schema accepts float,
  ge=0.5 enforced, decision record holds float, clamp preserves float,
  pipeline guard accepts float, bool rejected, decision_engine records
  float).

  **NOT changed** (out of scope for minimal fix):
  - `core/pre_production.py:621,635,649` still use `int(...)` for
    user-display minutes. Fractional precision still propagates through
    `est_minutes` but gets rounded at the display strings (`"X min"`).
    Acceptable; if display needs `.1f` formatting, that's a UI change.

Found and fixed in the same commit as the `core/pipeline_long.py` split
(Task 1) + `utils/crewai_breaker.py` (Task 2).

```
P4-18, P5-1, P5-2, P5-3, P5-4
```

- **P4-18.** Heavy/light scheduler slot wait — `light_semaphore.acquire(timeout=300)`
  in `utils/concurrency.py:54` raised spurious `TimeoutError` on long runs.
  Fixed: `300s → 60s`. (The heavy slot had already been raised to 1800s earlier
  in P2-1; the audit summary conflated the two and said the light was also at
  1800s. It wasn't, until P5-2 fixed it.)
- **P5-1.** `BreakerOpen` always reported `cooldown_s=0.0` (hardcoded at
  `utils/crewai_breaker.py:122`). Fixed: added
  `_BreakerState.cooldown_remaining_s()` public method
  (`utils/ollama_client.py`) and use it in `guarded_crewai_kickoff`.
- **P5-2.** WorkloadScheduler light-slot wait capped at 300s (same as P4-18 —
  P4-18 entry is the canonical record; P5-2 was the new ID during the refactor
  audit before they were merged).
- **P5-3.** `process_segment` closure was being built twice in
  `run_long_pipeline` (line 394 placeholder, line 438 real). Fixed: build once
  inside the `ThreadPoolExecutor` block.
- **P5-4.** Dead `from core.pre_production import _deep_merge` import in
  `utils/crewai_breaker.py:50`. Fixed: removed.

All 4 modified files import cleanly. `pytest tests/ -q` passes (235 tests).
Smoke test (`utils.crewai_breaker.guarded_crewai_kickoff`) now reports the
real remaining cooldown and the audit doc no longer lies about P3-20/P4-13.
