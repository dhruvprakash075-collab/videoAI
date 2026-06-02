# Pipeline Plan — Completed (2026-06-02)

> **Status:** ✅ Done. This plan called for security/style/complexity
> cleanups across the existing first-party Python files. As of 2026-06-02
> all 5 verification points are satisfied (`ruff check .` = 0 errors,
> 290/290 tests pass).

## Problems to Fix

### 1. Security Issues (19 total)

| Issue | Fix | Done? |
|-------|-----|-------|
| S110 (try-except-pass) | Add `log.debug()` to silent catches | ✅ |
| S310 (URL open audit) | Add scheme validation | ✅ |
| S324 (insecure hash MD5) | Replace with `hashlib.sha256` | ✅ (2026-06-02 in `agents/director_agent.py`) |
| S603 (subprocess call) | Add `shell=False` where possible | ✅ |

### 2. Style Issues (120+)

| Issue | Fix | Done? |
|-------|-----|-------|
| E501 (line too long) | Break lines at 100 chars | ✅ |
| E701/E702 (multiple statements) | Split to separate lines | ✅ |
| SIM117 (nested with) | Combine with statements | ✅ |
| RET505 (unnecessary else) | Remove else after return | ✅ |

### 3. Magic Numbers (50+)

| Issue | Fix | Done? |
|-------|-----|-------|
| Magic values (3, 50, 500, etc.) | Replace with named constants | ✅ |

### 4. Complexity (Keep as-is, just fix warnings)

| File | Issue | Action | Done? |
|------|-------|--------|-------|
| `core/pipeline_long.py` | C901 complexity 50 | Add `# noqa: C901` | ✅ |
| `bootstrap_pipeline.py` | C901 complexity 26 | Add `# noqa: C901` | ✅ |
| `audio/audio_proxy.py` | C901 complexity 18 | Add `# noqa: C901` | ✅ |

## Files fixed

| File | Fixes | Status |
|------|-------|--------|
| `agents/director_agent.py` | Security, style, magic numbers | ✅ |
| `audio/audio_fx.py` | Security, style | ✅ |
| `audio/audio_proxy.py` | Security, style, complexity | ✅ |
| `bootstrap_pipeline.py` | Complexity | ✅ |
| `core/pipeline_long.py` | Complexity | ✅ |
| `core/pre_production.py` | Style | ✅ |
| `core/segment_runner.py` | Style | ✅ |
| `config/config.py` | Style | ✅ |
| `memory/blackboard.py` | Style | ✅ |
| `memory/memory.py` | Style | ✅ |
| `studio_tui.py` | Style | ✅ |
| `tests/test_audio_crossfade.py` | Style | ✅ |

## Implementation Order

1. Fix security issues (S110, S310, S324, S603) — ✅
2. Fix style issues (E501, E701, E702, SIM117, RET505) — ✅
3. Replace magic numbers with constants — ✅
4. Add noqa comments for complexity warnings — ✅
5. Run `ruff check .` to verify — ✅

## Verification

```powershell
ruff check .  # Should show 0 errors  ← CONFIRMED 2026-06-02
pytest tests/ -q  # Should show 290 passed  ← CONFIRMED 2026-06-02
```

## What this plan did NOT call for

A previous version of `AGENTS.md` claimed this plan would "merge 8 scripts
into `pipeline.py` + `testpipeline.py`". That was a misreading. The
scripts under discussion — `bootstrap_pipeline.py`, `studio_tui.py`,
`utils/local_ui.py`, `train_lora.py`, `run_pipeline.py`, `run.bat`,
`launch_tui.ps1` — serve different audiences (CLI / TUI / web UI / LoRA
training / smoke test / Windows launcher / PowerShell wrapper) and are
better kept separate. A real consolidation done on 2026-06-02 was the
file-system cleanup of orphan test artifacts — see "File consolidation
(2026-06-02)" below.

## File consolidation (2026-06-02)

Root-level orphan files (no code reference) were moved to their proper
homes so the working tree stays clean:

| Was (root) | Now |
|------------|-----|
| `test_silence.wav` (5.3 MB) | `tests/fixtures/audio/silence_5s.wav` |
| `test_f5b.wav` (84 KB) | `tests/fixtures/audio/f5_sample.wav` |
| `test_omni.wav` (328 KB) | `tests/fixtures/audio/omnivoice_sample.wav` |
| `_ag_fullscreen.png` (35 KB) | `tests/fixtures/images/tui_fullscreen.png` |
| `_screen_now.png` (166 KB) | `tests/fixtures/images/tui_screen_capture.png` |
| `short_voice.pth` (55 MB) | `character_voices/short_voice.pth` |
| `empty_in.txt` (2 bytes) | deleted (empty stub) |
| `stdin_pipe.txt` (5 bytes) | deleted (empty stub) |
