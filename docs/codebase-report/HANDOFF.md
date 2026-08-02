# Handoff Prompt — Video.AI audit findings executor

Copy the block below into another agent (any capable coding agent) and let it
execute. It is self-contained; the report files carry the detail. All paths
are repo-relative from `C:\Video.AI`.

---

```
You are executing the Video.AI codebase-audit findings. All verdicts live in
docs/codebase-report/ — READ each file before acting on it. Full work order:

## 1. docs/codebase-report/bugs.md — items #1-43 (fix what is fixable)
Priority: #40 (IndicF5 TTS engine DOWN — huggingface_hub download error on
one-shot worker; fix HF route or switch config.yaml tts.engine to supertonic),
#41 (--words-per-segment CLI lock not honored — record locks 130 but critic
compares vs Writer-suggested 250; seed the writer target_word_count from the
DecisionRecord locked value), #38 (rules/ docs contradict reality: api-design.md
ghost /api/v1/* + /health contract, "Stable Diffusion" staleness, crashing
local_ui invocation, unregistered pytest markers, bandit mandate, 800-line
cap, .claude/ references), #37 (dead functions: get_temp_items/clear_temp_items
memory/project_store.py:992-1000; _call_ollama_streaming/_prewarm_ollama
agents/llm_client.py:130,199 + agents/director/llm_shims.py:50,53),
#35 (stale Modelfiles), #18 (Rust worker stale-blind — STALE_JOB_SECONDS only
a doc comment in rust/worker/src/lib.rs:19; needs Rust decision),
then #1-34, #36, #39, #42-43 in file order. #39 and #43 are notes, not fixes.

## 2. docs/codebase-report/unused.md — deletions
Delete ONLY the "Tracked — delete candidates" list (.gitlab-ci.yml +
GITLAB_SETUP_INSTRUCTIONS.md, coverage_baseline.txt, model_eval/ untrack+
gitignore, task_plan.md, vendors/indicf5/, Modelfile.* ×3, plans/README.md,
session-catchup.py verdict per #36) and the "Gitignored junk" list
(comfy_test_*.txt, color_output.png, fast_output.png, comfy_*_workflow.json,
director.db → move under data dir, jobs/_temp_content.txt). NEVER touch the
"Checked — NOT dead" list. When a dead module (bugs.md #6-10) is covered by
tests that keep it alive, delete those tests too (see item 4).

## 3. docs/codebase-report/duplicates.md — consistency fixes
- tts_capabilities(): audio/audio_proxy.py:1063 vs config/config.py:78
  _default_config() — identical body (token sim 1.00). Single source of truth
  in config, have audio_proxy read from it.
- Range/contract divergences: prompts.yaml:54 says image_count_per_segment
  "int 2-4" but prompts.yaml:145 says "int 5-12" — align both contracts;
  prompts.yaml:53/144 words_per_segment "100-400" vs DecisionRecord._clamp
  (50, 800) — align clamp to contract; images_per_segment clamp (1, 30) vs
  prompt ranges (2-4 / 5-12) — align. Default 130 vs prompt example 280 is
  cosmetic; leave.
- Rust hand-rolled SHA-256 (~200 lines, rust/worker/src/assets.rs, no sha2
  dep) — deliberate zero-dep choice, known-vector tests pass. Review
  candidate only: report, do NOT change without approval.

## 4. docs/codebase-report/test-audit.md — test fixes
- Vacuous test: tests/test_omnivoice_worker.py:7-10 test_set_seed_noop ends
  with bare `assert True` — remove the tautology (keep the no-raise intent).
- Dead-code keepalive: test_audio_fx.py, test_ip_adapter.py,
  test_retry_manager.py, test_web_search.py cover modules with 0 production
  importers — delete them together with their modules (item 2, bugs.md #6-10).
- Everything else in the file is clean (mock targets verified, skips
  inventoried) — no action.

## 5. docs/codebase-report/other-findings.md — advisory, no mandatory fix
- make_process_segment (core/segment_runner.py:1098) — cognitive 355, worst
  in repo; the tracked plan's PR4 refactor target. OPTIONAL large refactor:
  only attempt if you can keep the 2048-test suite green and the 6-arg
  signature contract (see bugs.md verified-clean note).
- Alloc-in-loop list (list_memory 7, enrich_prompts 6, WorldState.update 6,
  etc.) and remaining complexity hotspots — advisory; touch only if trivial.
- MCP crash note: server crashed mid-OPTIONAL-MATCH; reindex if needed
  (graph was current as of 2026-08-02).

## 6. docs/codebase-report/nested-loops.md — NO ACTION (verified clean)
3 trivial single-scan hits (web_search._strip_spoilers,
rust/worker/src/text.rs markdown_heading_boundaries, checkpoint.rs
clear_candidates); deep chains are structural batch fan-out. Do not "fix".

## 7. docs/codebase-report/python-scripts.md — NO ACTION (verified clean)
No orphaned scripts; all entries have callers/tests/doc references.

## 8. docs/codebase-report/usage.md — reference table
Verdicts power items 1-2 (UNWIRED/DEAD rows: audio_fx, ip_adapter,
retry_manager, web_search, media_analyzer, diagnose). Read before deleting.

## 9. docs/codebase-report/execution-log.md — evidence + log new work
Commands, timings and reproductions behind the verdicts. ADD ROWS for
anything you verify or re-run.

## 10. docs/codebase-report/README.md + docs/session-2026-08-02.md — context
Audit scope, pass history, real-run details (2026-08-02: 84s SUCCESS run,
findings #40-43). Read before starting; do not edit the session log.

## 11. PONYTAIL-DEBT.md (repo root) — 31 ponytail: markers
Debt ledger of deliberate shortcuts. Cross-check each marker against
bugs.md: markers already tracked there are covered; for the rest, decide
FIX or DEFER per item and report. Do not delete markers silently — update
the ledger entry when one is resolved.

## Hard constraints
- Follow AGENTS.md (lazy senior dev: shortest working diff, no new
  dependencies, no abstractions not requested, reuse existing helpers).
- NEVER bump pinned versions (torch, ComfyUI, ecosystem — pins exist for
  stub/CUDA/test stability). The Rust worker crate rules in AGENTS.md apply
  to Rust changes only; Python fixes may touch core/, video/, memory/ as the
  findings require.
- Never touch frontend dashboard/ (explicitly exempt).
- Do not touch config/comfyui/workflows/* or the vendored ComfyUI pin
  unless a finding says so.
- Cross-platform paths only (pathlib/os.path), no hardcoded backslashes.
- Deletion over addition. If a fix needs a schema change, STOP and ask.

## Verification (run all before declaring done)
- python -m pytest tests — full suite must stay green (baseline: 2048
  passed, 5 skipped).
- ruff check . — clean.
- mypy --follow-imports=skip --ignore-missing-imports agents/ui_state.py — clean.
- ComfyUI-touching changes: venv\Scripts\python.exe scripts\comfyui_smoke.py
  and record the result.
- Rust changes: cargo test && cargo clippy -- -D warnings && cargo fmt --check.
- After deletions: confirm compileall over tracked .py still exits 0.

## Report back
Per item (numbered as in this prompt): FIXED / SKIPPED (reason) /
NEEDS DECISION. One line per item. Flag anything where the fix breaks a
test or needs a pin bump — do not work around those, list them. Do not
commit unless asked.
```
