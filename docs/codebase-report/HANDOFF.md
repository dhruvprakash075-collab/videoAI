# Handoff Prompt — Video.AI audit findings executor

Copy the block below into another agent (any capable coding agent) and let it
execute. It is self-contained; the report files carry the detail.

---

```
You are executing the Video.AI codebase-audit findings. Work order:

## Work order (in priority order)

1. `docs/codebase-report/bugs.md` — items #1-43. Fix what is fixable,
   working top-to-bottom but with priority on: #40 (IndicF5 TTS engine
   fails — huggingface_hub download error; fix HF route or switch
   config.yaml tts.engine to supertonic), #41 (`--words-per-segment` CLI
   lock not honored by the script critic — seed writer target_word_count
   from the DecisionRecord locked value), #38 (agent-guidance rules
   contradict repo reality — see the per-item fixes in the file; the
   api-design.md ghost-contract section is the worst), #37 (dead functions:
   get_temp_items/clear_temp_items memory/project_store.py:992-1000,
   _call_ollama_streaming/_prewarm_ollama llm_client.py:130-199 +
   llm_shims.py:50-54), #35 (stale Modelfiles — delete or regenerate),
   #26-34 (see file).
2. `docs/codebase-report/unused.md` — delete list. Only delete items in
   "Tracked — delete candidates" + "Gitignored junk"; NEVER delete anything
   under "Checked — NOT dead". When a dead module is covered by tests that
   keep it alive, delete the tests too and re-run the suite.
3. `docs/codebase-report/README.md` — read for audit scope context.
4. `docs/codebase-report/execution-log.md` — read for evidence of the
   failure modes; add rows for anything you verify anew.

## Constraints (hard rules)

- Follow AGENTS.md (lazy senior dev: shortest working diff, no new
  dependencies, no abstractions not requested, reuse existing helpers).
- NEVER bump pinned versions (torch, ComfyUI, ecosystem — pins exist for
  stub/CUDA/test stability). No changes to bootstrap_pipeline.py, core/,
  video/, the SQLite schema, or the Rust worker crate rules in AGENTS.md.
- Do not touch frontend dashboard/ (explicitly exempt from audit).
- Do not touch config/comfyui/workflows/* or the vendored ComfyUI pin
  unless a finding says so.
- Cross-platform paths only (pathlib/os.path), no hardcoded backslashes.

## Verification (run all before declaring done)

- `python -m pytest tests` — full suite must stay green (2048 passed,
  5 skipped was the baseline).
- `ruff check .` — clean.
- `mypy --follow-imports=skip --ignore-missing-imports agents/ui_state.py`
  — clean.
- If you touched anything ComfyUI-related: run the real-instance smoke gate
  `venv\Scripts\python.exe scripts\comfyui_smoke.py` and record the result.
- If you touched the Rust crate: `cargo test && cargo clippy -- -D warnings
  && cargo fmt --check`.

## Report back

Per item: FIXED / SKIPPED (reason) / NEEDS DECISION. One line per item.
Flag anything where the fix would break a test or need a pin bump — do not
work around those, list them. Do not commit unless asked.
```
