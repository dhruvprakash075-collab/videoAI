# Reliability Cleanup — Plan 001 Finish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove dead `director_mode` plumbing, unused config fields, stale documentation, and Ruff findings. Zero new features, zero new dependencies.

**Architecture:** Pure deletion pass. Every removed field has zero production reads. Preserve Qwen routing, seed locking, base-frame fallback, and degradation reporting.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, React (dashboard), Rust (worker), Ruff.

---

## File Map

| Area | Files touched |
|------|--------------|
| CLI entry | `bootstrap_pipeline.py` |
| Pipeline sig | `core/pipeline_long.py`, `core/segment_runner.py` |
| UI | `utils/local_ui.py` + `dashboard/` |
| Local UI | `utils/local_ui.py` |
| Job worker | `jobs/worker.py` |
| Rust worker | `rust/worker/src/main.rs` |
| Dashboard | `dashboard/src/components/CreateJobPanel.jsx` |
| Config schema | `config/config_schemas.py` |
| Config YAML | `config/config.yaml` |
| Config defaults | `config/config.py` |
| Docs | `docs/AGENTS.md`, `docs/configuration_reference.md` |
| Ruff fixes | `agents/director_agent.py`, `tests/test_audio_proxy.py`, `tests/test_local_ui_api.py`, `video/image_gen/qwen_repose.py` |
| Tests | `tests/test_segment_runner_helpers.py`, `tests/test_segment_runner_extended.py`, `tests/test_phase0_fallbacks.py`, `tests/test_bootstrap_source.py`, `tests/test_local_ui_api.py`, `tests/test_operator_preferences.py` |

---

## Task 1: Remove `director_mode` from pipeline signatures and CLI

**Files:**
- Modify: `core/pipeline_long.py:124,529,669,703`
- Modify: `core/segment_runner.py:464`
- Modify: `bootstrap_pipeline.py:213,395,450`

- [ ] **Step 1: Remove from `core/pipeline_long.py`**

Remove `director_mode: bool = False` parameter from `run_long_pipeline()` signature (line 124). Remove `director_mode=director_mode` from the call to `make_process_segment()` (line 529). Remove `--director-mode` from the argparse block (line 669) and `director_mode=args.director_mode` from the result call (line 703).

- [ ] **Step 2: Remove from `core/segment_runner.py`**

Remove `director_mode: bool` parameter from `make_process_segment()` signature (line 464). The parameter is declared but never read inside the function body.

- [ ] **Step 3: Remove from `bootstrap_pipeline.py`**

Remove `--director-mode` argparse argument (line 213). Remove `director_mode=args.director_mode` from both `run_long_pipeline()` calls (lines 395, 450).

- [ ] **Step 4: Verify no remaining references in pipeline code**

Run: `rg -n "director_mode" core/ bootstrap_pipeline.py`
Expected: no matches.

---

## Task 2: Remove `director_mode` from TUI, Local UI, Job Worker, Rust Worker, Dashboard

**Files:**
- Modify: local UI run controls
- Modify: `utils/local_ui.py:172,354,399-401,456`
- Modify: `jobs/worker.py:113`
- Modify: `rust/worker/src/main.rs:1010`
- Modify: `dashboard/src/components/CreateJobPanel.jsx:50`

- [ ] **Step 1: Remove from local UI controls**

Remove the `if self._opt_director: kwargs["director_mode"] = True` block (line 837-838). Also remove the `_opt_director` attribute initialization if it exists elsewhere in the TUI class (search for `_opt_director`).

- [ ] **Step 2: Remove from `utils/local_ui.py`**

Remove `"director_mode"` from the boolean validation loop at line 172 (keep `"series"`). Remove `director_mode: str | None = Form(None)` parameter (line 354). Remove the `if director_mode is not None:` block (lines 399-401). Remove `director_mode` from the form parameter list at line 456.

- [ ] **Step 3: Remove from `jobs/worker.py`**

Remove `"director_mode"` from the `supported_args` set (line 113).

- [ ] **Step 4: Remove from Rust worker**

Remove `"director_mode"` from the argument filter match arm in `rust/worker/src/main.rs` (line 1010).

- [ ] **Step 5: Remove from dashboard**

Remove `if (directorMode) payload.director_mode = true;` from `dashboard/src/components/CreateJobPanel.jsx` (line 50). Also remove the `directorMode` state variable and its toggle UI if present (search for `directorMode` in the file).

- [ ] **Step 6: Verify no remaining active-code references**

Run: `rg -n "director_mode" --type py --type rs --type js --type jsx`
Expected: only patches/ and plans/ (historical).

---

## Task 3: Remove `director_mode` from tests

**Files:**
- Modify: `tests/test_segment_runner_helpers.py:618,662,712,1021,1067,1112`
- Modify: `tests/test_segment_runner_extended.py:47`
- Modify: `tests/test_phase0_fallbacks.py:121`
- Modify: `tests/test_bootstrap_source.py:290`
- Modify: `tests/test_local_ui_api.py:336`

- [ ] **Step 1: Remove `director_mode=False` kwargs from test calls**

In each test file, remove `director_mode=False` from function calls and dict literals. These are all test helper invocations that pass the now-removed parameter.

- [ ] **Step 2: Run affected tests**

Run: `python -m pytest tests/test_segment_runner_helpers.py tests/test_segment_runner_extended.py tests/test_phase0_fallbacks.py tests/test_bootstrap_source.py tests/test_local_ui_api.py -q`
Expected: all pass (parameter removal means callers no longer pass it).

---

## Task 4: Remove unused config fields from schema and YAML

**Files:**
- Modify: `config/config_schemas.py:367-368,412`
- Modify: `config/config.yaml:129-130,202`
- Modify: `config/config.py:81`

- [ ] **Step 1: Remove from `config/config_schemas.py`**

Remove `preview_steps: int = 12` and `oom_recovery: bool = True` from `ImageGenConfig` (lines 367-368). Remove `loudnorm_two_pass: bool = True` from `AudioFxConfig` (line 412).

- [ ] **Step 2: Remove from `config/config.yaml`**

Remove `preview_steps: 12` and `oom_recovery: true` from `image_gen:` block (lines 129-130). Remove `loudnorm_two_pass: true` from `audio_fx:` block (line 202).

- [ ] **Step 3: Remove `tts.slow` from `config/config.py`**

Remove `"slow": False` from the `tts` default dict (line 81). The TTS code never reads this field; it's a leftover from an earlier engine.

- [ ] **Step 4: Verify schema still validates config**

Run: `python -c "from config.config_schemas import PipelineConfig; import yaml; c=yaml.safe_load(open('config/config.yaml')); PipelineConfig(**c); print('OK')"`
Expected: OK.

---

## Task 5: Remove unused config fields from tests

**Files:**
- Modify: `tests/test_operator_preferences.py:57-61`
- Modify: `tests/manual_integration_test_b.py:8,58,261-275`
- Modify: `tests/manual_integration_test.py:45`

- [ ] **Step 1: Remove `preview_steps` test assertions**

In `tests/test_operator_preferences.py`, remove `test_preview_steps_matches_production` method (lines 57-61) — it asserts `preview_steps == steps` which is now a deleted field.

In `tests/manual_integration_test_b.py`, remove the `t_preview_steps` function (lines 262-275) and its check registration (line 304). Remove the `cfg["image_gen"]["preview_steps"] = 6` line (line 58). Update the docstring (line 8) to remove the `preview_steps` reference.

In `tests/manual_integration_test.py`, remove the `preview_steps` assertion (line 45).

- [ ] **Step 2: Run config tests**

Run: `python -m pytest tests/test_config.py tests/test_config_schemas.py -q`
Expected: all pass.

---

## Task 6: Remove stale documentation references

**Files:**
- Modify: `docs/AGENTS.md:107`
- Modify: `docs/configuration_reference.md:36,125-126`

- [ ] **Step 1: Update `docs/AGENTS.md`**

Remove the `loudnorm_two_pass` / `target_lufs` row from the verified ground truth table (line 107). The `target_lufs` field is still active — only remove the `loudnorm_two_pass` column reference. Update the row to only reference `target_lufs`.

- [ ] **Step 2: Update `docs/configuration_reference.md`**

Remove the `loudnorm_two_pass` row from the config table (line 36). Remove `preview_steps: 12` and `oom_recovery: true` lines from the image_gen example block (lines 125-126).

---

## Task 7: Fix Ruff findings

**Files:**
- Modify: `agents/director_agent.py:731`
- Modify: `tests/test_audio_proxy.py:4`
- Modify: `tests/test_local_ui_api.py:3`
- Modify: `video/image_gen/qwen_repose.py:398-403`

- [ ] **Step 1: Fix unused noqa directive**

In `agents/director_agent.py:731`, change `except Exception as exc:  # noqa: BLE001` to `except Exception as exc:` — the `BLE001` rule is not enabled, so the noqa is unused.

- [ ] **Step 2: Fix unused imports**

In `tests/test_audio_proxy.py:4`, remove `import subprocess` (unused). In `tests/test_local_ui_api.py:3`, remove `mock_open` from the import (unused; `MagicMock` and `patch` are still used).

- [ ] **Step 3: Fix nested if statements**

In `video/image_gen/qwen_repose.py:398-403`, combine nested `if` statements:

```python
# Before:
if not is_negative and "text" in inputs and ("prompt" in title or class_type.endswith("TextEncode")):
    if isinstance(inputs.get("text"), str):
        inputs["text"] = edit_prompt
if not is_negative and "prompt" in inputs and ("prompt" in title or "textencode" in class_type.lower()):
    if isinstance(inputs.get("prompt"), str):
        inputs["prompt"] = edit_prompt

# After:
if not is_negative and "text" in inputs and ("prompt" in title or class_type.endswith("TextEncode")) and isinstance(inputs.get("text"), str):
    inputs["text"] = edit_prompt
if not is_negative and "prompt" in inputs and ("prompt" in title or "textencode" in class_type.lower()) and isinstance(inputs.get("prompt"), str):
    inputs["prompt"] = edit_prompt
```

- [ ] **Step 4: Verify Ruff passes**

Run: `ruff check . --exclude .opencode`
Expected: 0 errors.

---

## Task 8: Final verification

- [ ] **Step 1: Run sequential test groups**

1. `python -m pytest tests/test_comfyui.py -q`
2. `python -m pytest tests/test_qwen_repose.py tests/test_image_gen.py -q`
3. `python -m pytest tests/test_config.py tests/test_config_schemas.py -q`
4. `python -m pytest tests/test_pipeline_graph.py tests/test_segment_runner_helpers.py tests/test_segment_runner_extended.py -q`
5. `python -m pytest tests/test_researcher.py tests/test_web_search.py tests/test_director_agent_helpers.py -q`

- [ ] **Step 2: Run Ruff on changed files only**

Run: `ruff check agents/director_agent.py tests/test_audio_proxy.py tests/test_local_ui_api.py video/image_gen/qwen_repose.py config/config_schemas.py core/pipeline_long.py core/segment_runner.py bootstrap_pipeline.py`
Expected: 0 errors.

- [ ] **Step 3: Final grep for removed fields**

Run: `rg -n "director_mode|preview_steps|oom_recovery|loudnorm_two_pass" --type py --type rs --type js --type jsx --glob '!patches/**' --glob '!plans/**' --glob '!docs/current_code_audit*'`
Expected: no matches in active code (only historical docs/plans/patches may retain references).

- [ ] **Step 4: Mark Plan 001 complete**

All sequential focused checks and Ruff pass. No full suite, no service startup, no model download, no GPU test.
