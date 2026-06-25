# Task-Wise Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the staged loop from segment-wise (each segment does script→translate→TTS→images→video) to task-wise batching (all scripts → all translations → all TTS → all images → all video within each staged batch), reducing Ollama load/unload cycles from ~15 to ~3 per batch.

**Architecture:** Keep the existing staged loop structure (batch by `lookahead_segments`). Within each batch, replace the concurrent segment processing with sequential task phases. Each phase runs all segments' same-type work before moving to the next phase. All data stays on disk (checkpoints, WAVs, PNGs, MP4s) — nothing held in RAM between phases.

**Tech Stack:** Python, LangGraph state machine (existing), ThreadPoolExecutor (existing), global_scheduler (existing)

---

## Complications Identified

| # | Complication | Risk | Mitigation |
|---|-------------|------|------------|
| 1 | **WorldState is order-dependent** — segment N's facts must be visible to segment N+1 | High | Run translate phase sequentially (1→2→3), not concurrent |
| 2 | **Director image review blocks rendering** — `important_image_review_node` runs between image and render | Medium | Include review in the image phase, before render phase |
| 3 | **Memory review uses Director LLM** — must run after render, uses Ollama | Low | Include in a final "finalize" phase after render |
| 4 | **Checkpoint resume** — each node checks for prior work on resume | Low | New task functions read same checkpoint keys |
| 5 | **HEAVY semaphore** — only 1 GPU slot, already serializes across concurrent segments | None | Task-wise removes concurrency, semaphore still works |
| 6 | **Supertonic worker** — persistent singleton, no change needed | None | Worker stays alive across phases |
| 7 | **`_director_abort` flag** — checked at segment start | Low | Check at start of each phase |

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `core/pipeline_long.py:565-585` | Modify staged loop | Orchestrate task phases within each batch |
| `core/segment_runner.py` | Add 5 new task functions | `run_scripts_phase`, `run_translations_phase`, `run_tts_phase`, `run_images_phase`, `run_renders_phase` |
| `tests/test_pipeline_long.py` | Add tests | Verify task-wise ordering and checkpoint interaction |
| `tests/test_segment_runner_helpers.py` | Add tests | Verify individual phase functions |

---

## Task 1: Extract per-task functions from process_segment

**Files:**
- Modify: `core/segment_runner.py` (after `log_vram_usage`, ~line 275)
- Test: `tests/test_segment_runner_helpers.py`

The current `process_segment()` runs the full LangGraph pipeline for one segment. We need standalone functions that run ONE task type across MULTIPLE segments.

- [ ] **Step 1: Write `run_scripts_phase(segments, config, outline, cp_mgr)`**

This function takes a list of segment indices, runs `write_script_node` + `critic_node` for each, and saves scripts to checkpoint. Reads segment plans from `outline["segments"]`.

```python
def run_scripts_phase(
    segment_indices: list[int],
    config: dict,
    outline: dict,
    cp_mgr,  # CheckpointManager
    topic: str,
) -> dict[int, str]:
    """Run script generation for multiple segments. Returns {seg_idx: script_text}."""
    results = {}
    for seg_idx in segment_indices:
        if _director_aborted():
            break
        # Build minimal SegmentState for write_script_node
        # Call write_script_node(state) + critic_node(state) 
        # Save to checkpoint via cp_mgr
        # Store result in results dict
    return results
```

- [ ] **Step 2: Write `run_translations_phase(segment_indices, config, outline, cp_mgr, topic)`**

Runs `translate_node` for each segment sequentially (order-dependent WorldState).

- [ ] **Step 3: Write `run_tts_phase(segment_indices, config, outline, cp_mgr, topic)`**

Runs `tts_node` for each segment. Can be parallel since TTS is independent, but HEAVY semaphore serializes anyway.

- [ ] **Step 4: Write `run_images_phase(segment_indices, config, outline, cp_mgr, topic)`**

Runs `image_node` + `important_image_review_node` for each segment.

- [ ] **Step 5: Write `run_renders_phase(segment_indices, config, outline, cp_mgr, topic)`**

Runs `render_node` + `memory_review_node` for each segment.

- [ ] **Step 6: Write tests for each phase function**

Verify: phases read from checkpoint, write outputs, handle abort flag.

- [ ] **Step 7: Run tests, commit**

---

## Task 2: Refactor staged loop to task-wise phases

**Files:**
- Modify: `core/pipeline_long.py:565-585`
- Test: `tests/test_pipeline_long.py`

- [ ] **Step 1: Replace segment-concurrent batch processing with task phases**

Current code (lines 565-585):
```python
for _batch in _batches:
    evict_ollama_models(config, reason="C1 staged batch")
    _batch_futures = {executor.submit(_process_segment_with_budget, _bi): _bi for _bi in _batch}
    for _bf in concurrent.futures.as_completed(_batch_futures):
        ...
```

New code:
```python
for _batch in _batches:
    if get_director_abort():
        break
    if _bi > 0:
        start_ollama_server(config, reason=f"batch {_batch}")

    # Phase 1: Scripts (Ollama loads once for writer)
    evict_ollama_models(config, reason="C1 scripts phase")
    run_scripts_phase(_batch, config, outline, cp_mgr, topic)

    # Phase 2: Translations (Ollama loads once for translator)
    evict_ollama_models(config, reason="C1 translations phase")
    run_translations_phase(_batch, config, outline, cp_mgr, topic)

    # Phase 3: TTS (CPU, no Ollama needed)
    evict_ollama_models(config, reason="C1 TTS phase")
    run_tts_phase(_batch, config, outline, cp_mgr, topic)

    # Phase 4: Images + review (ComfyUI loads once)
    evict_ollama_models(config, reason="C1 images phase")
    run_images_phase(_batch, config, outline, cp_mgr, topic)

    # Phase 5: Renders + memory review (Ollama loads once for director)
    evict_ollama_models(config, reason="C1 renders phase")
    run_renders_phase(_batch, config, outline, cp_mgr, topic)

    if _bi < len(_batches) - 1:
        stop_ollama_server(config, reason=f"after batch {_batch}")
```

- [ ] **Step 2: Update imports in pipeline_long.py**

Add imports for new phase functions from `segment_runner`.

- [ ] **Step 3: Write tests verifying task-wise ordering**

Test that within a batch, scripts complete before translations start, etc.

- [ ] **Step 4: Run all tests, commit**

---

## Task 3: Verify checkpoint compatibility

**Files:**
- Test: `tests/test_pipeline_long.py`
- Test: `tests/test_qwen_repose.py`

- [ ] **Step 1: Test resume from mid-batch checkpoint**

Simulate: scripts done for seg 1-3, pipeline crashes, restart. Verify seg 1-3 scripts are loaded from checkpoint, seg 4-6 start fresh.

- [ ] **Step 2: Test Qwen resource gate still works with task-wise batching**

Verify that image phase checks RAM/VRAM and falls back to one-pass when gate fails.

- [ ] **Step 3: Run full focused test suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_pipeline_long.py tests/test_segment_runner_helpers.py tests/test_qwen_repose.py tests/test_image_gen.py -q
```

- [ ] **Step 4: Ruff check**

```powershell
.\venv\Scripts\python.exe -m ruff check core/segment_runner.py core/pipeline_long.py
```

- [ ] **Step 5: Commit**

---

## Task 4: Clean up and document

**Files:**
- Modify: `docs/qwen_image_edit_setup.md` (update batch processing section)
- Modify: `docs/configuration_reference.md` (note task-wise batching behavior)

- [ ] **Step 1: Update docs**
- [ ] **Step 2: Final full test suite**
- [ ] **Step 3: Commit**

---

## Verification

After implementation, run:
```powershell
# Focused tests
.\venv\Scripts\python.exe -m pytest tests/test_pipeline_long.py tests/test_segment_runner_helpers.py tests/test_qwen_repose.py tests/test_image_gen.py tests/test_config_schemas.py tests/test_preflight.py -q

# Full Ruff
.\venv\Scripts\python.exe -m ruff check core/segment_runner.py core/pipeline_long.py

# Dashboard tests (unchanged, should still pass)
cd dashboard && npm run test:run
```

Expected: All tests pass, Ruff clean, Ollama loads reduced from ~15 to ~3 per batch.
