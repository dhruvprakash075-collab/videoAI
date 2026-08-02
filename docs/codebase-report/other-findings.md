# Other Findings — 2026-08-02

## Complexity hotspots (cognitive >= 35, non-test)

- `core/segment_runner.py:1098 make_process_segment` — cognitive **355**, complexity **155**, alloc-in-loop 4 — worst function in the repo; prime refactor target (the tracked plan's PR4).
- `utils/scene_director.py enrich_prompts` — 143 / 46, alloc 6.
- `memory/memory.py WorldState.update` — 127 / 32, alloc 6.
- `core/pre_production.py run_pre_production` — 101 / 49.
- `utils/local_ui.py save_ui_config` — 100 / 34.
- `config/config_schemas.py validate_config` — 99 / 23, alloc 4.
- `utils/local_ui.py list_memory` — 95 / 26, **alloc 7** (top allocator).
- `agents/director/config_production.py produce_runtime_config` — 87 / 43.

## Alloc-in-loop smells (allocation inside loop = GC pressure; >= 4)

- `list_memory` 7, `enrich_prompts` 6, `WorldState.update` 6, `video/renderer/renderer.py build_html` 5, `make_process_segment` 4, `validate_config` 4, `video/image_gen/comfyui_client.py wait_for_completion` 4, `utils/web_search.py search_story_web` 4, `utils/scene_director.py assemble_prompt_multi` 4, `utils/source_splitter.py _split_by_chapter` 4.

## Repo hygiene

- `agents/decision_engine.py` + `tests/test_decision_engine.py` — were modified-uncommitted at audit start; since committed (bb6e6411c, c9a59b525). Working tree clean; only `docs/codebase-report/` untracked.
- codebase-memory MCP server crashed mid-query (during `OPTIONAL MATCH`); graph is otherwise current (re-indexed 2026-08-02).

## Verdict

Nothing here blocks shipping. `make_process_segment` is the only function worth
a dedicated refactor; the rest are advisory. No security findings in this pass.
