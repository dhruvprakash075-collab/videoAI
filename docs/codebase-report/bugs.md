# Bugs & Breaks — equal-weight findings

Every finding below was reproduced or verified at source. No severity ranking —
all are equally reportable. **Execution status (2026-08-02)**: findings #1-43
were executed per HANDOFF.md — see HANDOFF.md "Execution status" appendix for
per-item verdicts. This file remains the audit-time record; execution-log.md
carries the post-fix evidence, including the second-pass corrections (worker
path bootstrap, QC wording).

## CLI contract breaks (entry points that misbehave)

1. **`jobs/worker.py` ignores `--help` (and all args) and starts the worker loop.**
   `if __name__ == "__main__": w = Worker(); w.run_forever()` (worker.py:295-297)
   — no argument parsing at all. Running `.\venv\Scripts\python.exe -m jobs.worker --help`
   hung for 12+ s (poll loop, no output) and had to be killed. An operator
   asking for help gets a running worker. Same for `jobs/run_worker.py`.
   Evidence: execution-log.md.

2. **`scripts/comfyui_smoke.py` ignores `--help` and executes a real ComfyUI
   workflow.** Running `scripts\comfyui_smoke.py --help` during this audit ran
   the full smoke end-to-end: it spawned/used a live ComfyUI and produced a
   frame (`scene_01.png`, 533 136 bytes, "SMOKE OK"). No argument parsing in
   the script — any args are silently dropped. This is both a CLI bug and a
   "runs real models" surprise for anyone invoking it with arguments.

3. **`utils/preflight.py` breaks when run standalone.**
   `.\venv\Scripts\python.exe utils\preflight.py` → check functions fail with
   `ModuleNotFoundError: No module named 'config'` (and `director_model`).
   It only works when repo root is on `sys.path` (i.e., invoked through
   `bootstrap_pipeline.py` or `-m`). Its `__main__` block advertises standalone
   use. Fix: insert repo root into `sys.path` in `__main__` (or document
   `-m utils.preflight`).

4. **Mixed invocation conventions, undocumented.** `python core/pipeline_cli.py`
   → `ModuleNotFoundError: No module named 'core'`; only `python -m core.pipeline_cli`
   works. Same for `jobs/worker.py` / `jobs/run_worker.py` (`config`/`jobs`).
   Direct-script invocation is used by `bootstrap_pipeline.py`, `scripts/*`,
   `tools/*` and works; `core/*`, `jobs/*` require `-m`. No doc or guard
   explains which is which — the failure mode is a confusing traceback, not a
   helpful message.

5. **`utils/diagnose.py` rejects `--help`.** `utils/diagnose.py --help` →
   `[ERROR] Unknown diagnostic command: --help` (rc=1), because `--help` is
   dispatched as a diagnostic name. Usage is printed only after the error, and
   a valid diagnostic (`gpu`, `media`, `system`, `all`) is required first.

## Unwired production code (config promises, code doesn't deliver)

6. **`audio/audio_fx.py` never runs.** `audio_fx.enabled: true` in
   `config/config.yaml` + schema + 3 tests, but 0 production importers. SFX
   mixing and premium voice processing are silently absent from every pipeline
   run. Either wire it in or delete the config keys and module. (The loudnorm
   part is reimplemented inline in `video/renderer/assembler.py` — that half
   works, which makes the silent gap harder to notice.)

7. **`video/image_gen/ip_adapter.py` never runs.** `ip_adapter_scale` config and
   the `ip_adapter_ref` review decision exist, but the adapter is never loaded;
   character face-consistency silently never happens.

## Dead code (safe to delete; tests keep it alive)

8. `utils/retry_manager.py` — superseded by `core/segment/retry.py`.
9. `utils/web_search.py` — deprecated (story.py:51); only tests import it.
10. `utils/media_analyzer.py` — CLI-only tool, no prod callers (also the only
    gateway to the opt-in Rust audio analyzer, `_native_analyze_audio_wave`).
11. `utils/diagnose.py` — CLI-only, referenced by no launcher or doc.

## Environment (not code, but blocks/paints runs)

12. **PATH `python` is 3.14.5 (`C:\Python314`)** — violates the project's
    `requires-python >=3.10,<3.14` (pyproject.toml). `bootstrap_pipeline.py`
    correctly rejects it (venv guard). The sanctioned interpreter
    `.\venv\Scripts\python.exe` (3.12.13) is not on PATH — anyone typing
    `python bootstrap_pipeline.py` gets the venv-guard error.
13. **VRAM is under the preflight floor**: 2.6/6.0 GB free vs 4.5 GB required
    for SD (preflight check). Ollama's resident models are the likely consumer.
    Runs will fail preflight until freed.

## Silent-failure sweep — result: 0 true silent failures

23 candidate swallow sites located by AST, every one read at source and
verified as a deliberate, guarded fallback:
- `consultation.py:349` + `:228` — skip malformed tokens from LLM replies,
  defaults filled later (benign).
- `llm_client.py:180`, `ollama_client.py:207`, `audio_proxy.py:262/324/447/538/610/769`,
  `utils/utils.py:366` — JSON-line protocol skips with timeout/error handling
  around them.
- `bootstrap_pipeline.py:82/89/108/118` — Windows console/rich/tqdm guards.
- `check_environment.py:147` — nvidia-smi parse skip.
- `preflight.py:219` — two-command playwright fallback.
- `url_security.py:93` — control-flow fallthrough into the localhost check.
- `sentry.py:46`, `main.py:15`, `pipeline_long.py:51`, `compatibility.py:38`,
  `test_no_broad_suppress.py:43` — import/console guards.
No path silently loses data or swallows a fatal error. `utils/errors.py`
defines a strict error taxonomy and the suite (2048 passed) exercises it.

## Second pass — Rust worker, config schemas, YAML, workflows (all verified live)

14. **`utils/local_ui.py` crashes when run as a script.** `python utils/local_ui.py
    --help` → `ModuleNotFoundError: No module named 'agents'` rc=1 — same
    invocation-convention family as #3/#4. It works only via `-m utils.local_ui`.
15. **`python -m utils.local_ui --help` ignores `--help` and starts a live uvicorn
    server on 127.0.0.1:8000** (killed by timeout; no listener left). No argument
    parsing at all — an operator asking for help gets a running server.
16. **`cargo run --help` in `rust/worker` → rc=101 "could not determine which
    binary"** — no `default-run` in Cargo.toml despite 4 binaries
    (`videoai-worker` is the main one). Users must know `--bin videoai-worker`;
    cargo's error doesn't say which binary exists.
17. **Rust `doctor` resolves the repo root from CWD** — running
    `cargo run --bin videoai-worker -- doctor --json` from `rust/worker`
    reports `bootstrap_pipeline` "missing C:\Video.AI\rust\worker\bootstrap_pipeline.py"
    (critical) and `config_yaml` "missing ...\config\config.yaml" (warn).
    False failures; the message doesn't say "run from repo root". The comfyui
    checks skip with "image backend is not comfyui" — reads like an environment
    finding, but is just "comfyui not configured" (misleading skip detail).
18. **Rust implements NO stale-job detection.** `STALE_JOB_SECONDS = 120`
    exists only as a doc comment (`rust/worker/src/lib.rs:19`); the Rust worker
    loop is stale-blind, yet AGENTS.md/duplicates.md claim the constants are
    "mirror-exact" with `jobs/worker.py`. The doc promises behavior the code
    doesn't have.
19. **`_director_vision` is silently dropped by pydantic round-trips.**
    Field `config_schemas.py:505` (leading underscore = pydantic private attr,
    allowlisted :587): `model_dump()` excludes it and
    `ConfigOverlay(**{'_director_vision': {...}})` keeps theme='' — input
    silently discarded (verified live, pydantic 2.12.5). Prod readers
    (`core/director_memory.py:44`, `core/pre_production.py:200`) and writer
    (`agents/director/config_production.py:885`) use raw dicts, so it works
    today — but any future overlay round-trip silently loses vision settings.
    Latent data-loss trap.
20. **`IndicF5SubConfig().root` default = `D:\IndicF5`** (config_schemas.py)
    while `config/config.yaml:22` uses `external\IndicF5`. Schema default
    points at a drive that doesn't exist on this machine.
21. **Hardcoded absolute Windows paths**: `comfyui_nodes/video_ai_nodes/helpers.py:12`
    `DEFAULT_REPO_ROOT = r"C:\Video.AI"` and `config/config.yaml:22-23`
    (`external\IndicF5`, `C:\Video.AI\venv\Scripts\python.exe`). Broken for
    other checkouts and CI.
22. **Dead schema fields** — 0 consumers anywhere: `max_images_per_segment`
    (config_schemas.py:208, ge=0) and `uncapped_scaling` (:209). Config keys
    that silently do nothing (same family as #6/#7 at schema level). Used
    neighbors: `max_words` (:207), `tts_words_per_minute_hi/en` (:204-205).
23. **Prompt/config drift — TTS engine**: vision prompt hardcodes
    `"tts_recommendation": "omnivoice"` (prompts.yaml ~:43) while runtime
    config is `tts.engine: indicf5` (config.yaml). The director will
    recommend an engine the config doesn't use.
24. **Prompt/config drift — segment length**: prompts.yaml:20 tells the model
    "~1 min per segment" while config `segment_length_minutes` is 2.
25. Minor: `jobs/job_store.py:7-8` — `DB_PATH` relative to CWD with `mkdir`
    at import time (CWD-dependent side effect on import).

## Third pass — config.yaml full read, hot path, CI, docs (all verified live)

26. **README.md:64-67 documents broken Rust commands** — `cargo run
    --manifest-path rust/worker/Cargo.toml -- list-jobs` fails rc=101
    ("could not determine which binary to run"; verified live). Finding #16
    is not a UX nit: the repo's own quick-start fails. Fix: `--bin
    videoai-worker` or `default-run`.
27. **Dead SD-era cache helpers in `video/image_gen/image_gen.py`**:
    `_prompt_cache_key` (:199), `_master_portrait_hash_for_frame` (:179),
    `_maybe_upscale` (:235), `_current_project_id` (:196, never assigned).
    Zero production callers — kept alive only by test_image_gen.py. The
    docstring (:8-9) advertises them as public surface. Leftovers from the
    diffusers/SD backend; the ComfyUI path never caches or upscales via them.
28. **YouTube upload exception unguarded** (core/post_production.py:376-400):
    when `upload.enabled: true`, a Playwright/upload crash propagates out of
    `finalize_production` — completed video exists, but run reports error and
    no manifest is written (manifest write happens after upload). Should be
    wrapped like the other finalize steps.
29. **Docs stale in 3 places**: `manga_identity_pose_api.json` referenced by
    system_architecture.md:51, configuration_reference.md:122,
    runtime_safety_guide.md:84 — but config.yaml:171 uses
    `manga_ipadapter_style_api.json`. configuration_reference.md:44 shows
    `root: D:\IndicF5` (config uses `external\IndicF5`); README.md:81 says
    "1940 passed, 1 failed" and testing_and_linting.md:12 says "2060 passed"
    — actual: 2048 passed / 5 skipped; configuration_reference.md:26 claims a
    top-level `language` key that doesn't exist in config.yaml.
30. **Third `D:\IndicF5` hardcode**: `core/preflight.py:50` fallback default
    (plus config_schemas.py:IndicF5SubConfig and configuration_reference.md).
    Three sources disagree with config.yaml's `external\IndicF5`.
31. **Hardcoded workflow node indices** (image_gen.py:521-522): refine pass
    pokes `wf["1"]["inputs"]["image"]` and `wf["11"]["inputs"]["filename_prefix"]`.
    Verified correct against the current JSON (1=LoadImage, 11=SaveImage) but
    silently wrong if the refine workflow is ever renumbered. No
    class-type assertion, unlike WorkflowPatcher.
32. Nits: `assembler.py:434` dead `if True:` block; `renderer.py:190` dead
    `sum(...)` expression; `pipeline_long.py:512` fallback default 6 vs
    config `default_images_per_segment: 2`; `pyproject.toml:146-154` stale
    "2618-line god module" ignores for director_agent.py (now a 99-line
    facade); config.yaml:241/261 user_agent has literal `...` placeholder.
33. **Missing config-file references, both benign**: `character_voices/
    narration_ref_9s_mono24k.txt` (ref_text_file) absent — inline ref_text
    fallback verified (audio_proxy.py:121-125); `chrome_profile/` absent —
    upload disabled. Everything else referenced by config.yaml exists on disk
    (checkpoints, VAEs, LoRAs, reference images, IndicF5, ComfyUI models).
34. **Non-finding confirmed**: `performance.max_segment_retries` IS honored in
    staged mode via `_retry_segment_phase` (segment_runner.py:903-920) — the
    two retry wrappers (staged vs graph) are redundant but both live.

## Fourth pass — gap closure re-check (user: "close gap and check again")

35. **Modelfile.* build recipes stale/broken**: all three `FROM C:\models\*.gguf`
    targets are absent on disk (verified False ×3: zephyr-7b-beta.Q4_K_M,
    Hermes-3-Llama-3.1-8B-Q4_K_S, cra-v1-guided-7b-Q4_K_M). Runtime does not
    use them — `models.director: hermes-director` (config.yaml:2) is an Ollama
    model name; README.md:25 pulls it from the registry; `Modelfile.cra-guided`
    has zero references anywhere. `ollama create -f` on any of them fails.
    Delete, or regenerate against the current model sources.
36. **usage.md count drift resolved**: report claims "228 tracked .py";
    ground truth is 229 — the extra file is
    `.opencode/skills/planning-with-files/scripts/session-catchup.py`
    (dot-dir tooling, excluded from the pass-1 sweep). No production file was
    ever missed; the table's per-dir glob rows cover the rest.

## Fifth pass — function-level dead-code sweep (MCP graph) + agent-guidance rules audit

37. **Dead functions found via the knowledge graph** (degree-0 / tests-only,
    previously invisible to the module-level sweep because the modules are
    used):
    - `memory/project_store.py:992,997` — `clear_temp_items` +
      `get_temp_items` on `PermanentMemoryLog`: zero callers in all 229
      tracked .py (internal `_temp_items` dict is read inline at :930).
      Delete both.
    - `agents/llm_client.py:130,199` + `agents/director/llm_shims.py:50,53`
      — `_call_ollama_streaming` ("token-by-token stream for live UI
      feedback", llm_client.py:13) and `_prewarm_ollama` ("background
      warm-up of director + writer models", llm_client.py:14): only tests
      call them; the shim→client edges exist but the shim roots are
      uncalled. Two documented features never wired into production. Either
      wire (UI feedback) or delete.
    - Verified NOT dead (graph false positives): `get_system_status`,
      `get_voices`, `get_chat_session` (local_ui.py) — FastAPI route
      handlers registered via decorator (graph doesn't link decorator);
      `__init__` ×3 (constructor edges target the class node);
      `_cleanup_proc` ×2 (self-calls missed).
38. **Agent-guidance rules contradict repo reality** (`rules/`, 24 files):
    - `rules/fastapi/api-design.md:90-106` — documents `/api/v1/pipeline/*`
      + `/health` endpoints; `utils/local_ui.py` has ZERO `api/v1` and ZERO
      `/health` routes (grep-verified). The documented API contract does not
      exist; an agent following it builds against ghosts. Rewrite to the
      actual `/api/*` routes or delete the section.
    - `rules/common/codebase-onboarding.md:43` + `rules/common/code-tour.md:38`
      — "Stable Diffusion for image generation": stale; image gen is ComfyUI
      (SD-era code is dead, see #27).
    - `rules/common/codebase-onboarding.md:90` — prescribes
      `venv\Scripts\python.exe utils\local_ui.py`, which crashes with
      `No module named 'agents'` (#14). Should be `python -m utils.local_ui`.
    - `rules/python/testing.md` + `testing-advanced.md` — teach
      `@pytest.mark.unit/integration` + `--strict-markers`; the repo
      registers no markers (pyproject has only `testpaths`) and organizes by
      directory. Following the rule verbatim fails on unknown markers.
    - `rules/python/security.md` — mandates bandit; not installed, not in
      any CI workflow.
    - `rules/common/coding-style.md` — "files <800 lines"; repo has
      segment_runner.py (1126) and assembler.py (1060).
    - `rules/common/eval-harness.md` — references `.claude/evals/`; no
      `.claude/` dir exists (repo uses `.agents/`, `.cursor/`, `.opencode/`).
    - `.opencode/skills/planning-with-files/SKILL.md` — path references point
      at `$HOME/.claude/...` and `~/.config/opencode/...` copies of the skill;
      the tracked copy is `C:\Video.AI\.opencode\...`. Multi-copy layout,
      harmless but confusing.
    - Consistent (no action): git-workflow (conventional commits match git
      log), patterns (real `guarded_crewai_kickoff`/`BreakerOpen` imports),
      performance (heavy 1/1800s, light 16/60s match concurrency.py),
      .cursor/ponytail.mdc (matches AGENTS.md), .agents skills (hashes match
      skills-lock.json), pyrightconfig + rust-toolchain (match CI pins).
39. **MCP server operational note**: codebase-memory-mcp v0.10.0 is alive
    (6561 nodes / 22671 edges); `query_graph` CRASHES the server on
    aggregate Cypher (OPTIONAL MATCH + count — the same failure class that
    took it down mid-audit), `search_graph` works. Function-level sweeps
    should use `search_graph` with `min/max_degree`, not raw Cypher.
40. **Real run (2026-08-02): IndicF5 TTS engine fails; silent fallback.**
    `bootstrap_pipeline.py --topic ... --segment-count 1 --words-per-segment
    130 --yes --no-resume` (84s video SUCCESS): `[IndicF5] Calling one-shot
    worker...` → huggingface_hub `HTTPStatusError` (client error downloading
    model assets) → auto-degraded to supertonic (CPU ONNX, worked). IndicF5
    is the configured primary engine (`config/config.yaml` `tts.engine:
    indicf5`) and is DOWN in this environment (HF download fails; probably
    blocked/absent network route). Every real run silently burns a failed
    IndicF5 attempt + ~30s before falling back. Action: fix HF access for
    IndicF5, or switch `tts.engine` to `supertonic`.
41. **Real run: `--words-per-segment` CLI lock not honored by the script
    critic.** DecisionRecord locked `words/seg=130 (cli_flag)`, but the
    segment-script critic compared against the Writer's suggested 250
    (`[DECISION ENGINE] Writer adjusted 'words_per_segment' → 250` before the
    CLI lock overrode the record): `script word count 120 deviates from
    target 250 (tolerance ±20%) — rejecting for rewrite`, twice — while the
    writer was already producing ~130-word scripts (120, then 146). Result:
    2 wasted rewrite cycles (~2×30s of LLM time) then "Max rewrites reached.
    Proceeding with unapproved script." The lock propagates to the Decision
    Record but NOT to the writer/critic target. Fix: seed the writer
    task's target_word_count from the DecisionRecord's locked value.
42. **Real run: QC "FAIL" severity doesn't fail the run.** Planned 120s
    (Director) vs actual 84s (TTS narration 1:23.9) → `Quality check: FAIL —
    Duration mismatch` logged as WARNING, pipeline still SUCCESS. On short
    single-segment runs the Director's duration estimate (~2.0min) will
    routinely overshoot TTS length, so every such run prints FAIL. Either
    recompute the QC baseline from actual narration length, or downgrade to
    a warning that doesn't say FAIL.
43. **Real run perf note (not a bug)**: refine/upscale phase
    (`manga_refine_upscale_api.json`, FaceDetailer + upscale) took ~85s per
    image — 8 images ≈ 12 of the 18 total minutes. The dominant cost of a
    short run. Optional knob: `config/config.yaml` refine skip toggle or
    fewer refinement passes for one-shot runs.

## Verified clean

- No stale references to deleted symbols (`VideoAIConfig`, legacy V1
  `INPUT_TYPES`, `director_agent.input` bridge) — compileall + import smoke
  (all prod modules import clean on venv) + full test suite green.
- No `config["x"]` direct-dict-indexing in prod (all `.get()`).
- `make_process_segment` 6-arg signature consistent between
  `segment_runner.py:64` and the single caller in `pipeline_long.py`
  (test_2026_06_fixes.py asserts exactly one call).
- `negative_prompt` present in `config/config.yaml:179` — schema-consistent.
