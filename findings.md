# Findings — Storyboard-Prompt-Builder Audit

## Verified architecture facts (from code read)

### Pre-production / pipeline flow
- `run_pre_production()` at `core/pre_production.py:52-61` — signature `(topic, config, skip_consultation=False, content_text=None, force_refresh=False, project_name=None, cli_flags=None, run_mode="one_time") -> dict` (config_overlay)
- `plan_outline()` at `core/pre_production.py:434` — runs once before segment loop, produces raw outline
- `run_long_pipeline()` at `core/pipeline_long.py:310` — thin orchestrator
  - Line 475: `outline = plan_outline(topic, n_segs, config, director_agent, cp_mgr, resume)`
  - Line 477: `_adjust_outline_length(...)`
  - Lines 497-501: `shape_outline(...)` — FINAL outline
  - Line 526+: segment closure build — storyboard hook goes BETWEEN 501 and 526
- `make_process_segment` at `core/segment_runner.py` — per-segment loop: script → TTS → build_prompts → enrich_prompts → generate_images → render

### Memory system (`memory/project_store.py`)
- `ProjectStore` — `project.json` shared across stories: characters, visual_locks, memory_items
  - `set_character_assets` (line 323): `character_sheet_path`, `face_reference_path`, `full_body_reference_path`, `identity_hash`
  - `get_character_assets` (line 357): returns all above + `is_approved`
  - `get_master_portrait_path` (line 240), `get_master_portrait_hash` (line 248)
- `StoryStore` — `story.json` is a **plain dict** (no schema rejection for new fields)
  - Defaults in `_load_story`: segments, world_facts, open_threads, characters, motifs, memory_items
  - `_save_story` caps segments at 100
  - Lives at `studio_projects/{project}/stories/{story}/` or `_one_time/{story}/`
- `PermanentMemoryLog` — backward-compat shim routing to ProjectStore + StoryStore

### Consultation (`agents/director/consultation.py`)
- `consult_user(question, options=None, allow_custom=True)` — line 19
  - `--yes` → `UIState.auto_accept` → returns `options[0]` without prompting
  - UI mode → `UIState.pause_event.wait(timeout=300)` → timeout auto-defaults
  - Non-interactive (no TTY) → auto-defaults
  - CLI → numbered menu, max 50 attempts, empty input → default
- `ConsultationMixin` is inherited by `DirectorAgent` — `director_agent.consult_user(...)` works

### LLM access (`agents/llm_client.py`)
- `DirectorLlmClient` constructed in `DirectorAgent.__init__` as `self.llm` (director_agent.py:90)
- `_call_ollama(prompt, model_type="director", format_json=False, seed=None)` — line 65
  - Returns cleaned text or `""` on failure (never None)
  - Delegates to `utils.ollama_client.get_ollama_client(...)`
- `_call_ollama_chat(prompt, model_type="translator", system_msg=...)` — line 88

### Image gen (`video/image_gen/image_gen.py`)
- `_reference_pool` (lines 67-76) — returns config reference paths, does NOT resolve master portraits
- `_stable_character_reference` (lines 99-122) — DOES resolve master portraits, uses `_reference_pool` as seed
- `generate_images` (line 132) — public API, dispatches to `_comfyui()` (line 162)
- `reference_usage: direct` (config.yaml:190) — gates reference usage; non-direct returns None
- Panel compositor: `compose_panel_pages` (panel_compositor.py:188), `plan_page_counts` (recently added)

### Config (`config/config_schemas.py`)
- `ALLOWED_KEYS` (line 593) — whitelist of top-level keys, must add "storyboard"
- `SECTION_MODELS` (line 561) — maps sections to pydantic models, must add StoryboardConfig
- `extra: forbid` on individual schema classes — new fields need schema additions
- `validate_config(raw_config)` (line 612) — entry point

### Scene director (`utils/scene_director.py`)
- `enrich_prompts(raw_prompts, script, config, plan=None, memory_items=None)` — line 92
  - **Already takes `plan` param** — shot metadata can ride in it without signature change
  - Reads `plan.get("char_presence")` — could add `plan.get("shot_metadata")`
- `assemble_prompt(identity_tokens, scene_tokens, style_tokens, budget=70)` — line 469
- `assemble_prompt_multi(identity_list, scene_tokens, style_tokens, budget=70)` — line 357
- `_detect_mood(script)` — line 550, keyword matching

### Director agent (`agents/director_agent.py`)
- `DirectorAgent` (line 68) — facade over 8 mixins
- `__init__(self, llm_config, memory=None)` (line 80) — constructs `self.llm = DirectorLlmClient(llm_config)`
- `self._prompts` loaded via `_load_prompts()` (line 98-99)

## Key design decisions
1. Hook AFTER `shape_outline` (line 501), NOT after `plan_outline` — outline must be final
2. Reuse `_stable_character_reference` for sheet-as-reference, NOT `_reference_pool`
3. Use `ProjectStore.get_character_assets` for real character ref images when present
4. No `enrich_prompts` signature change — shot metadata rides in existing `plan` dict
5. `director_agent.llm._call_ollama` for single LLM call — no new client wiring
6. StoryStore story.json is plain dict — safe to add `storyboard` field