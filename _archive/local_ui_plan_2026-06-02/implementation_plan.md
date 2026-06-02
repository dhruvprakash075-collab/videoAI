# Implementation Plan

[Overview]
Harden and align the local UI backend (`utils/local_ui.py`) with the pipeline’s concurrency/VRAM safety and thread-safety expectations, while ensuring the existing API response shapes remain stable and tests continue to pass.

This plan focuses on verifying architectural invariants (VRAM single-model rule, scheduler semantics, thread-safe UI state reads/writes) and then applying minimal, low-risk changes limited to `utils/local_ui.py`.

[Types]  
Single sentence describing type system changes: No type system changes required.

No new types are required beyond using existing FastAPI request parameter types (`int`, `str`) and existing in-memory job structures for A/B.

[Files]
Single sentence describing file modifications: Modify only `utils/local_ui.py` (no refactors elsewhere).

Detailed breakdown:
- New files to be created:
  - None
- Existing files to be modified:
  - `utils/local_ui.py`
    - Thread-safe read of `UIState.logs` in `GET /api/status`
    - Add VRAM/LLM eviction safety (or equivalent) to background A/B generation worker in `POST /api/ab/generate`
- Files to be deleted or moved:
  - None
- Configuration file updates:
  - None

[Functions]
Single sentence describing function modifications.

Detailed breakdown:
- New functions:
  - None (optional: a small helper to copy logs under lock could be inlined or added as a private function, but no external API changes)
- Modified functions:
  - `get_system_status()` in `utils/local_ui.py`
    - Current: `logs_obj = getattr(UIState, "logs", [])` then `logs_list = list(logs_obj)[-100:]` without synchronization
    - Required change: copy `UIState.logs` under `UIState._log_lock` (or fall back safely if lock is unavailable), then take the last 100 items from the copied list
    - Ensure response shape remains exactly:
      - `{status, active_question, logs, output_video}`
  - `_run_ab(job_id, pa, pb)` inner worker in `POST /api/ab/generate`
    - Current: wraps `generate_images` in `global_scheduler.task("heavy", ...)` but does not perform Ollama eviction / VRAM release like the main segment loop
    - Required change: before calling `generate_images` for each variant (or once before both variants), perform a VRAM-protection step consistent with the main pipeline:
      - Call `core.segment_runner.evict_ollama_models(config, reason="UI-AB")` if feasible and safe to import
      - If `evict_ollama_models` import is too costly or fails, implement a best-effort fallback that only clears CUDA cache (if torch available), still within the heavy scheduler context
    - This must remain low-risk:
      - No changes to job status transitions (`running -> generating_a/b -> ready/error`)
      - No changes to `images_a`/`images_b` URL construction format
      - Preserve existing input validation behavior for `segment_num`

- Removed functions:
  - None

[Classes]
Single sentence describing class modifications.

Detailed breakdown:
- No class modifications required. `UIState` remains the single shared state provider.

[Dependencies]
Single sentence describing dependency modifications.

Details of new packages/version changes:
- No dependency changes.
- Use only already-imported modules and existing project utilities (`core.segment_runner.evict_ollama_models`, `utils.concurrency.global_scheduler`, `utils.load_config`).

[Testing]
Single sentence describing testing approach.

- Run `ruff check .` to ensure lint stays green.
- Run full test suite: `venv\Scripts\python.exe -m pytest tests/ -q` (target: 281 passed).
- Add/validate manual “API level” checks (no new automated tests required by this plan):
  - `GET /api/status` returns JSON with `logs` always as a list (even during concurrent writes)
  - `POST /api/ab/generate` rejects invalid `segment_num` and maintains traversal protections
  - Ensure A/B generation does not crash under concurrent load (best-effort due to GPU variability)

[Implementation Order]
Single sentence describing the implementation sequence.

Numbered steps showing logical order:
1. Inspect current `utils/local_ui.py` code paths for `/api/status` and A/B generation worker (confirm response shapes and current validation).
2. Implement thread-safe `UIState.logs` copying in `get_system_status()` using `UIState._log_lock` when available; keep fallback behavior if lock is missing.
3. Implement VRAM/LLM eviction safety in the A/B worker before Stable Diffusion generation, using `core.segment_runner.evict_ollama_models(config, reason="UI-AB")` and best-effort CUDA cache clearing fallback.
4. Run `ruff check .` and `pytest tests/ -q` to confirm no regressions.
5. Execute a minimal runtime smoke test for `/api/status` and `/api/ab/*` endpoints (manual curl/Invoke-RestMethod), verifying response shapes and error codes.
