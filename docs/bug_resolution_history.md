# Bug Resolution History

This document consolidates confirmed historical bug fixes with references to live code. Fixes are tracked via the historical `B`-series IDs (B1–B40) and current `P`-series IDs.

> **Status**: All 78 historical P-series bugs have been successfully resolved, and legacy bug audit files (`BUGS.md` and `BUGS_AUDIT_2026-05.md`) have been deleted from the root workspace to keep the repository clean. New bug reports should be assigned the next sequential P-id.

---

## 1. Key Individual Bug Fixes

| Bug ID | Area | Fix |
|---|---|---|
| **B1** | Ollama Circuit Breaker | Established `OllamaClient._breaker` per-model state machine in `utils/ollama_client.py`. |
| **B13** | Retry Tiers | Added two-tier retry in `utils/retry_manager.py`: transient errors (ConnectionError, TimeoutError) get 50 retries; bounded errors (RuntimeError, OSError) get max 3 retries. |
| **B14** | Retry Scope | `generate_images` is **not** wrapped by the outer retry manager — it has its own internal 3-tier OOM recovery. Wrapping it externally would cause compounding multi-minute hangs. |
| **B15** | CrewAI Lock | Added shared `crewai_lock = threading.RLock()` in `utils/concurrency.py` to serialize all `crew.kickoff()` calls and prevent litellm executor corruption. |
| **B32** | SFX Bundling | `_DEFAULT_SFX` keyword has no effect unless a matching WAV is in `sfx/`. Only `thunder.wav` is bundled. Drop a matching WAV in `sfx/` for wind/rain/heartbeat to activate. |

---

## 2. P-Series Fixes (2026-05 → 2026-06)

| Bug ID | Fix |
|---|---|
| **P3-14** | Changed `crewai_lock` from `threading.Lock` to `threading.RLock` (prevents deadlock when the same thread re-acquires). |
| **P4-8** | `audio_fx.enabled` flipped to `true` in `config/config.yaml` to enable bundled SFX. |
| **P4-18** | LIGHT slot wait reduced from 300s → **60s** (`utils/concurrency.py`). |
| **P4-23** | `BreakerOpen.cooldown_s` now returns the **real** remaining cooldown (was hardcoded 0). |
| **P4-27** | `run_pipeline.py` hardcoded "Real Hero" smoke test. |
| **P5-1** | `BreakerOpen` real-cooldown contract — `cooldown_s` is the actual remaining seconds from `OllamaClient._breaker()`. |
| **P5-2 to P5-4** | Additional 2026-06-01 fix sweep — see `tests/test_2026_06_fixes.py` (25 regression tests). |

---

## 3. Recent Changes (2026-06-02)

- **Security**: Replaced MD5 with SHA256 for cache keys in `agents/director_agent.py`.
- **Smoke-test fix**: `core/post_production.py` line 193 — import path of `log_vram_usage` corrected from `core.pre_production` → `core.segment_runner` (stale Phase 0 reference).
- **God-module split**: `UIState` + `_devanagari_ratio` moved to `agents/ui_state.py`; Ollama plumbing moved to `agents/llm_client.py`. Both are re-exported from `director_agent.py` so existing imports remain valid.
- **v6 pipeline shipped**: `--source <path-or-URL>` CLI flag, 6 new modules (`source_loader`, `source_splitter`, `researcher`, `critic`, `seo_generator` extended, `bootstrap_pipeline._load_and_split_source`). 304 new tests added (total: 290 → 613 → grew further with extended coverage).
- **Ruff linting**: 2,400 raw errors auto-fixed + manually triaged. Ruff caught 6 real latent bugs during cleanup. All checks pass.

---

## 4. Research Sources (Confirmed)

The v6 web research module (`utils/researcher.py`) uses exactly **3 sources**:
1. **Wikipedia REST API**
2. **Wikimedia REST API**
3. **RSS feeds**

These are configurable via `config.yaml` under the `research:` section. DuckDuckGo is **not** used in this codebase.
