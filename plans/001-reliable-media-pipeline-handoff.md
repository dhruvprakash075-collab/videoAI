# Plan 001: Make the media pipeline truthful and reliable

> **Executor instructions**: This is a self-contained implementation handoff. Read it fully before changing code. Execute phases in order and run only the focused verification command attached to the phase. Do not run the complete Python test suite, start model servers, download models, or perform GPU inference unless the operator explicitly authorizes that action. If a STOP condition occurs, stop and report it instead of improvising.
>
> **Drift check (run first)**:
> `git diff --stat 2ba9b863..HEAD -- video/image_gen config core agents audio utils tests dashboard requirements.txt`
>
> If an in-scope file changed, compare the current implementation with the facts below. Update this plan before executing it if the contracts or call paths have changed.

## Status

- **Priority**: P1
- **Effort**: L, split into independently reviewable phases
- **Risk**: MEDIUM; Qwen and pipeline-routing changes affect generated media but should not affect saved project formats
- **Depends on**: none
- **Category**: correctness, tests, tech debt, product reliability
- **Planned at**: commit `2ba9b863`, 2026-06-21

## Outcome

The supported production path should become:

1. DreamShaper 8 generates a complete background through ComfyUI.
2. The Director's scene plan supplies `char_presence` for each frame.
3. Deterministic routing selects the image operation:
   - no qualifying character: keep the DreamShaper frame;
   - qualifying character: run Qwen-Image-Edit with the background and saved character reference;
   - unavailable or failed Qwen: preserve the base frame and record a visible degradation.
4. Supertonic generates narration.
5. FFmpeg assembles the video with the existing Ken Burns path.

This plan deliberately does **not** give an LLM unrestricted control over model selection. The Director decides scene content and character presence; ordinary code converts that structured decision into a repeatable model route. This is cheaper, testable, and cannot hallucinate an unsupported backend name.

## Non-negotiable product decisions

- Fix Qwen before adding any dashboard controls.
- Do not wire Qwen into the web UI in this plan.
- Do not install a new multipart dependency; the standard library is sufficient.
- Do not claim that a feature ran merely because its configuration was enabled.
- Every fallback must be visible through logs and the existing degradation ledger.
- Never run multiple heavy verification jobs concurrently.
- Do not run the entire Python test suite as part of this plan. Use the focused commands listed below.
- Do not start Ollama or ComfyUI automatically during unit tests.
- Real GPU validation is a separate, explicitly authorized one-frame acceptance step.

## System map and current behavior

### Image dispatch

- `video/image_gen/image_gen.py:126-215` reads the global image backend and composition mode.
- `backend == "comfyui"` with `composition_mode == "qwen_edit"` and `qwen_edit.enabled == true` attempts `_comfyui_qwen_edit()`.
- The default committed configuration is `backend: comfyui`, `composition_mode: one_pass`, and `qwen_edit.enabled: false` in `config/config.yaml:125-182`.
- Any non-ComfyUI backend currently falls into Bonsai. The existing Replicate and Pexels functions at the bottom of `image_gen.py` are not dispatched.

### Qwen flow

- `video/image_gen/image_gen.py:785-838` first runs ordinary ComfyUI generation and then calls `repose_character()` only for frames whose dominant character meets the configured threshold.
- `video/image_gen/qwen_repose.py:350-424` resolves the character reference, patches the Qwen workflow, submits it to ComfyUI, and currently returns the base frame after most failures.
- `config/comfyui/workflows/qwen_image_edit_api.json:1-18` uses two standard `LoadImage` nodes for the background and character reference.
- `video/image_gen/comfyui_client.py:83-90` exposes `upload_image()` but raises `NotImplementedError` instead of performing multipart upload.
- `qwen_repose.py:395-404` patches filesystem paths directly into the `LoadImage` workflow. It does not stage either file into ComfyUI's input store.
- The dashboard and `/api/config` intentionally remain out of scope. At present, `utils/local_ui.py:797-801` accepts only `one_pass` and `layered_v3`.

### Director and character review

- The Director produces scene plans containing `char_presence`; `core/segment_runner.py:996-1002` forwards it into image generation.
- There is no Director decision selecting `one_pass`, `qwen_edit`, `layered_v3`, Bonsai, Replicate, or Pexels.
- `director_mode` reaches `make_process_segment()` but is not read after the parameter declaration at `core/segment_runner.py:464`.
- Important-image review occurs after image generation. With the default `hermes-director` model, `_is_vision_model()` is false, so `agents/director_agent.py:566-582` performs text-only review using metadata rather than image pixels.
- `lora_candidate` and `ip_ref` decisions are stored by `memory/project_store.py`, but there is no tracked LoRA training implementation.

### Other disconnected or misleading surfaces

- `video/image_gen/framepack_i2v.py` has no production caller. `tests/test_motion_engine.py:42-83` simulates a call rather than exercising pipeline dispatch.
- `audio/audio_proxy.py:959-982` invokes `utils/rvc_worker.py`, which does not exist. `RvcConfig` contains only `enabled`, so required model paths cannot pass strict configuration validation.
- `utils/researcher.py:276` defines the configured researcher but has no production caller. `agents/director_agent.py:702-721` uses the separate `utils.web_search` path and does not honor `research.enabled`, source selection, or budget.
- `audio/audio_fx.py:64` defines `mix_sfx()`, but production code does not call it. Final program loudness is handled separately in `video/renderer/assembler.py`.
- Music assembly accepts a track in `core/post_production.py:252-273`, but `MusicConfig` forbids the `track_path` and `mood_tracks` fields that code reads.
- Layered V3 settings `approval_mode`, `closeup_threshold`, and `max_characters` are persisted but never read by `video/image_gen/layered_v3.py`.
- `script.critic_enabled` and `critic.enabled` do not control graph construction; `core/pipeline_graph.py:144` always routes the writer into the critic.
- `image_gen.lock_seed` is not consumed. `image_gen.py:715-726` does not pass a seed to `WorkflowPatcher.patch_all()`, which chooses a random seed when omitted.
- The Real-ESRGAN upscaler is called only from the Bonsai code path. It does not process default one-pass ComfyUI output.

## Supported, deferred, and removed capability policy

| Capability | Decision | Reason |
|---|---|---|
| DreamShaper one-pass | Keep | Proven primary background generator |
| Qwen character insertion | Repair first | Directly serves character composition and identity |
| Bonsai | Keep as explicit fallback | Useful when ComfyUI fails; do not call it the primary consistency mechanism |
| IP-Adapter | Keep only with Bonsai | Current implementation attaches it only to Bonsai |
| Ken Burns/FFmpeg | Keep | Low-resource, established renderer |
| Qwen web-UI controls | Defer | Operator explicitly requested no UI wiring now |
| Layered V3 | Defer or delete | Overlaps Qwen and exposes unused controls |
| FramePack | Defer | No production integration; high GPU cost for a 6-GB target |
| Replicate/Pexels | Delete unless explicitly required | Dead code and misleading backend surface |
| RVC | Remove unless a worker/model contract is explicitly funded | Current worker is missing and schema cannot configure it |
| LoRA training | Defer | Candidate metadata exists, but training/loading is a separate product |
| SFX | Connect only after an explicit product decision | Current mixer is isolated and the asset catalog is incomplete |
| Background music | Repair schema only if music is wanted | Runtime mixing exists, but configuration cannot supply tracks |

## Commands permitted for this plan

Run commands sequentially, never concurrently.

| Purpose | Command | Expected success |
|---|---|---|
| Qwen unit tests | `venv\Scripts\python.exe -m pytest tests/test_qwen_repose.py tests/test_image_gen.py tests/test_comfyui.py -q` | all selected tests pass |
| Config tests | `venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_config_schemas.py -q` | all selected tests pass |
| Routing tests | `venv\Scripts\python.exe -m pytest tests/test_pipeline_graph.py tests/test_segment_runner_helpers.py tests/test_segment_runner_extended.py -q` | all selected tests pass |
| Research tests | `venv\Scripts\python.exe -m pytest tests/test_researcher.py tests/test_web_search.py tests/test_director_agent_helpers.py -q` | all existing selected tests pass; if `test_researcher.py` does not exist, create it in the research phase |
| Focused lint | `venv\Scripts\python.exe -m ruff check <only files changed in the current phase>` | exit 0 |
| Drift/scope | `git status --short` | only phase-scoped files plus pre-existing user files are shown |

Do not use `pytest -q` without an explicit test-file list. Do not run GPU model inference in automated tests.

## Phase 1 — Repair ComfyUI image upload

### Objective

Make `ComfyUIClient.upload_image()` perform the local ComfyUI `/upload/image` request and return the validated JSON response expected by a `LoadImage` workflow.

### Files in scope

- `video/image_gen/comfyui_client.py`
- `tests/test_comfyui.py`

### Required implementation

1. Build multipart/form-data using only standard-library modules such as `uuid`, `mimetypes`, and `urllib.request`.
2. Include these form fields:
   - binary `image` field with a safe filename;
   - `type=input`;
   - `overwrite=true` only when the caller intentionally requests overwrite.
3. Reuse the client's existing local-service URL validation and circuit-breaker behavior. Do not concatenate an unvalidated remote URL.
4. Reject a missing file before any request.
5. Parse the JSON response and require a non-empty `name`. Preserve `subfolder` and `type` when returned.
6. Do not log image bytes, authentication data, or full multipart bodies.
7. Remove the unconditional `NotImplementedError` and the unused `multipart` import.

### Tests

Add focused tests using a mocked `urllib.request.urlopen` or the same request mocking pattern already used in `tests/test_comfyui.py`:

- existing image creates a POST request to `/upload/image`;
- request content type contains `multipart/form-data` and a boundary;
- body contains the filename, `type=input`, and the image bytes;
- valid response returns `name`, `subfolder`, and `type`;
- missing input file raises `FileNotFoundError` without a network call;
- malformed JSON or missing `name` raises a clear runtime error;
- circuit-breaker behavior remains intact.

### Verification

1. `venv\Scripts\python.exe -m pytest tests/test_comfyui.py -q`
2. `venv\Scripts\python.exe -m ruff check video/image_gen/comfyui_client.py tests/test_comfyui.py`

Expected: both commands exit 0.

## Phase 2 — Stage Qwen inputs and report real outcomes

### Objective

Upload the DreamShaper background and saved character reference before submitting the Qwen workflow, patch `LoadImage` with ComfyUI-recognized input names, and make success/fallback counts observable.

### Files in scope

- `video/image_gen/qwen_repose.py`
- `video/image_gen/image_gen.py`
- `tests/test_qwen_repose.py`
- `tests/test_image_gen.py`

### Required implementation

1. In `repose_character()`, call `client.upload_image()` for both the base image and character reference after `runtime.ensure_running()` succeeds.
2. Convert each upload response into the string expected by `LoadImage`:
   - `name` when `subfolder` is empty;
   - a normalized forward-slash `subfolder/name` when a subfolder is returned;
   - never pass the original host filesystem path to standard `LoadImage`.
3. Change `_patch_qwen_workflow()` parameters so their names describe ComfyUI input references rather than arbitrary filesystem paths. Patch nodes 1 and 2 with those references.
4. Keep the output destination behavior unchanged: final edited frames must still be copied back to the requested frame path.
5. Keep preflight failures non-fatal, but make them visible. They should produce a degradation entry and a warning containing the missing prerequisite names.
6. For a Qwen request that begins after preflight passes:
   - preserve the base image if an individual frame fails;
   - record a degradation through `UIState.add_degradation()` using a stable stage such as `qwen_edit_fallback`;
   - count attempted, edited, skipped-no-character, and failed frames;
   - emit one batch summary at the end.
7. Do not report a frame as edited unless the generated output exists and was copied successfully.
8. Do not make an individual frame failure crash the full video render.
9. Do not silently reroute the whole batch to Bonsai after some DreamShaper frames have already been produced. Preserve successful Qwen frames and clearly retain base images for failed frames.

### Tests

- both input images are uploaded exactly once for a non-cached Qwen edit;
- workflow receives uploaded names, not original filesystem paths;
- returned subfolders are normalized correctly;
- cache hit performs no upload and no generation request;
- no-character frame performs no Qwen upload;
- upload failure returns/preserves the base image and records degradation;
- ComfyUI generation failure preserves the base image and records degradation;
- successful output is copied to the requested path and counted as edited;
- batch summary counts edited, skipped, and failed frames accurately.

### Verification

1. `venv\Scripts\python.exe -m pytest tests/test_qwen_repose.py tests/test_image_gen.py -q`
2. `venv\Scripts\python.exe -m ruff check video/image_gen/qwen_repose.py video/image_gen/image_gen.py tests/test_qwen_repose.py tests/test_image_gen.py`

Expected: both commands exit 0. No service or GPU should be required.

## Phase 3 — Make Qwen routing deterministic

### Objective

Use Director-produced `char_presence` as structured input to a deterministic router. Do not ask the Director LLM to invent backend names.

### Files in scope

- `video/image_gen/image_gen.py`
- `config/config_schemas.py`
- `config/config.yaml`
- `tests/test_image_gen.py`
- `tests/test_config_schemas.py`

### Target contract

- `backend` remains `comfyui` for DreamShaper backgrounds.
- Qwen remains guarded by `qwen_edit.enabled` and valid preflight.
- `qwen_edit.trigger` must become a real validated enum rather than an ignored string. Recommended values:
  - `any_character`: edit frames whose dominant character meets `character_threshold`;
  - `disabled`: never edit, even if the mode is selected.
- `character_threshold` remains the sole numeric routing threshold.
- `max_resolution` and `youtube_aspect` must either be implemented with tests or removed. Recommended action: remove them until a real transformation consumes them.
- `composition_mode` remains globally configurable for now; do not add web-UI wiring.

### Important architectural clarification

The Director decides which characters are present and their importance weights. The router decides whether those values require Qwen. This still lets the Director influence Qwen use without making model selection nondeterministic.

### Tests

- no character above threshold: Qwen is skipped;
- one character above threshold: Qwen runs;
- multiple characters: current single-dominant-character limitation is explicit and tested;
- trigger disabled: Qwen is skipped;
- Qwen disabled globally: ordinary one-pass runs;
- preflight failure: ordinary DreamShaper frame remains and degradation is recorded.

### Verification

Run the Qwen and configuration test commands from the permitted-command table.

## Phase 4 — Make seed locking truthful

### Objective

Ensure repeated inputs produce repeated ComfyUI workflow seeds when locking is enabled.

### Files in scope

- `video/image_gen/image_gen.py`
- `video/image_gen/comfyui_workflow.py`
- `config/config_schemas.py`
- `tests/test_image_gen.py`
- `tests/test_comfyui.py`

### Required behavior

1. Define one seed resolution rule:
   - explicit non-negative `image_gen.seed`: use it as the batch base;
   - `lock_seed: true` with no explicit seed: derive a stable base seed from stable inputs such as project/frame/prompt;
   - `lock_seed: false`: request a random seed.
2. Pass the resolved seed into `WorkflowPatcher.patch_all()` and `create_default_workflow()`.
3. Use a deterministic per-frame offset so frames differ while reruns remain reproducible.
4. Apply the same seed contract to Qwen cache keys.
5. Do not use Python's process-randomized `hash()`; use a stable digest from `hashlib`.

### Tests

- same prompt/frame/config with locking enabled yields the same seed;
- different frame indices yield different stable seeds;
- explicit seed overrides derived seed;
- locking disabled allows the random-seed path;
- workflow JSON contains the resolved seed.

## Phase 5 — Consolidate research into one controlled path

### Objective

Make the `research` configuration govern the actual research performed by the Director.

### Files in scope

- `agents/director_agent.py`
- `utils/researcher.py`
- `utils/web_search.py` only for deletion or compatibility cleanup
- `tests/test_researcher.py` (create if absent)
- `tests/test_director_agent_helpers.py`

### Required behavior

1. `DirectorAgent.research_story()` must call `utils.researcher.research_topic(topic, config)`.
2. The Director must receive or resolve the validated full configuration, not a partial LLM-only configuration that omits `research`.
3. `research.enabled: false` must cause zero network calls and return an empty normalized result.
4. Respect configured sources, RSS URLs, request budget, timeout, and per-source limit.
5. Adapt `ResearchItem` objects to the Director's existing research dictionary shape at one boundary.
6. Remove or clearly deprecate the duplicate `utils.web_search` execution path once no production caller remains.
7. Network errors must degrade to empty research with a visible warning; they must not block story generation.

### Tests

- disabled research performs no calls;
- configured sources are called in order within budget;
- results are normalized into the Director's expected structure;
- one failing source does not discard successful sources;
- empty result remains a valid Director input.

## Phase 6 — Make switches truthful

Implement each item as a separate small commit and focused test.

### Critic enablement

Files: `core/pipeline_graph.py`, `core/segment_runner.py`, `config/config_schemas.py`, related tests.

- Select one source of truth: `critic.enabled`.
- Remove `script.critic_enabled` and the duplicate threshold/rewrite controls from `script` after migrating their values.
- When disabled, route writer directly to translation.
- When enabled, preserve the existing critic/rewrite loop.
- Test both graph routes without invoking an LLM.

### Director mode

Files: CLI/job/TUI argument plumbing and tests.

- Recommended action: delete `director_mode` because it has no defined behavior.
- Remove it from CLI parsing, job allowlists, UI job forms, TUI switches, and function signatures in one change.
- If the operator supplies an explicit desired behavior before implementation, stop and replace deletion with a separately specified plan.

### Checkpoint and image tuning no-ops

The following configured fields had no production read at the planned commit:

- `performance.checkpoint_interval`
- `image_gen.preview_steps`
- `image_gen.oom_recovery`
- `image_gen.layered_v3.character_dir`
- `audio_fx.loudnorm_two_pass`
- `tts.slow`

For each field, either connect it with a focused behavioral test or delete it from schema/config/UI. Recommended action is deletion unless the operator states a required behavior.

## Phase 7 — Remove or explicitly defer feature shells

This phase should happen only after Qwen is stable.

### Layered V3

- It overlaps the Qwen composition objective.
- `approval_mode`, `closeup_threshold`, and `max_characters` are currently no-ops.
- Its committed workflow paths are empty, so selecting it falls back to one-pass.
- Recommended action: remove the dashboard option and configuration surface, then delete the implementation only after confirming no project overlay selects `layered_v3`.
- STOP if any saved project configuration uses it; report those project names without exposing unrelated content.

### FramePack

- No production caller exists.
- Existing tests simulate dispatch and therefore cannot detect the missing integration.
- The current target is a 6-GB GPU, making simultaneous image-generation and motion-model residency risky.
- Recommended action: remove the config keys and simulated tests, retain Ken Burns, and leave FramePack recoverable through Git history.
- If the operator explicitly requires AI motion, create a separate GPU-calibrated spike plan rather than wiring it casually.

### Replicate and Pexels

- `_replicate()` and `_pexels()` are dead functions in `image_gen.py`.
- Dispatcher and UI support only ComfyUI/Bonsai behavior.
- Recommended action: delete both functions and remove the `replicate` dependency if no other tracked import uses it.
- Do not add API keys or cloud configuration merely to preserve unused code.

### RVC

- The worker script is missing.
- Strict schema cannot hold model/index paths used by `rvc_convert()`.
- Dashboard defaults to skipping it.
- Recommended action: remove RVC configuration, CLI/job switches, and conversion code.
- If voice conversion is a product requirement, stop and request model format, inference runtime, model location, licensing, and expected GPU/CPU budget before rebuilding it.

### LoRA candidates

- Keep stored candidate metadata only if it has independent review value.
- Rename UI/log language so it does not promise training.
- Do not build training now; Qwen reference editing and Bonsai IP-Adapter must be validated first.
- A future LoRA plan must specify dataset curation, minimum images, training implementation, output storage, loading path, trigger words, GPU budget, and rollback.

### SFX and music

- Do not call `audio_fx.enabled` a working SFX switch until `mix_sfx()` is connected to narration before final assembly.
- If SFX is desired, connect one proven asset first, record missing-asset degradation, and test FFmpeg argument construction without running expensive media jobs.
- If music is desired, add validated `track_path` and `mood_tracks` fields to `MusicConfig`; otherwise remove the disabled section.
- Keep final program loudness normalization separate from optional SFX enablement.

## Phase 8 — Acceptance and truthfulness checks

### Automated acceptance

Run only focused test groups sequentially. Then verify:

- no `NotImplementedError` remains in `ComfyUIClient.upload_image()`;
- Qwen workflow receives uploaded ComfyUI names rather than host paths;
- Qwen failures create degradation records;
- Qwen batch summary reports attempted/edited/skipped/failed counts;
- seed-lock tests prove reproducibility;
- disabled research makes zero network calls;
- disabled critic bypasses the critic node;
- deleted settings are absent from schema, committed config, UI, and tests;
- no dead backend is shown to users or accepted by configuration.

### One-frame hardware acceptance — explicit authorization required

Do not run this automatically. When the operator explicitly approves:

1. Confirm no other GPU-heavy process is running.
2. Start only ComfyUI.
3. Use one existing saved character reference and one generated background.
4. Run one Qwen edit at the configured resolution.
5. Confirm logs show two successful uploads and one edited frame.
6. Confirm the output path exists and differs from the base image.
7. Record elapsed time and peak VRAM.
8. Stop ComfyUI if this test started it.
9. Do not proceed to a multi-frame run until this passes.

### Runtime status disclaimer

At plan creation, Ollama was unreachable and ComfyUI was not running, although the configured ComfyUI installation and DreamShaper checkpoint existed. This is environment state, not proof that either implementation is broken. The complete Python test suite was not established as a safe baseline and must not be claimed as passing.

## Git workflow

- Suggested branch: `codex/reliable-qwen-pipeline`
- Use one commit per phase or independently reversible subsection.
- Follow the repository's conventional commit style, for example `fix: upload Qwen inputs through ComfyUI`.
- Do not push or open a pull request unless the operator explicitly requests it.
- Preserve all unrelated untracked and modified user files.

## Done criteria

All of these must hold before calling the plan complete:

- [ ] Qwen base and reference images are uploaded through a working standard-library multipart client.
- [ ] Standard `LoadImage` nodes receive ComfyUI input names, never arbitrary host paths.
- [ ] Qwen frame failures preserve the base frame and create a visible degradation.
- [ ] Batch logs distinguish attempted, edited, skipped, and failed frames.
- [ ] Qwen remains absent from dashboard and web-UI configuration controls.
- [ ] Character-presence routing is deterministic and covered by focused tests.
- [ ] Seed locking produces stable tested workflow seeds.
- [ ] Research configuration controls the Director's actual research path.
- [ ] Critic enablement has one source of truth and changes graph routing.
- [ ] `director_mode` is either given an explicit tested behavior or removed.
- [ ] Dead/deferred capabilities are removed from active configuration and user-facing controls, or explicitly marked experimental with a real preflight check.
- [ ] No full-suite, service startup, model download, or GPU test was run without explicit authorization.
- [ ] Every phase's focused tests and Ruff checks pass.
- [ ] No files outside the phase scope were modified.

## STOP conditions

Stop and report without improvising if:

- ComfyUI's installed upload endpoint has a different contract from `/upload/image` with an `image` multipart field.
- The installed Qwen custom nodes require a loader other than standard `LoadImage`.
- A Qwen edit requires changing final frame paths consumed by Rust or FFmpeg.
- Supporting multiple characters requires more than the current dominant-character contract.
- Any phase requires a new dependency.
- A focused test unexpectedly starts Ollama, ComfyUI, a model download, FFmpeg encoding, or GPU inference.
- An implementation step requires dashboard Qwen wiring.
- Saved project configurations actively use a feature recommended for deletion.
- Resource usage becomes unexpectedly high; terminate the single active check and report which command caused it.

## Maintenance notes

- Treat logs and degradation records as part of the product contract: operators must be able to distinguish an edited frame from a retained base frame.
- Keep model routing deterministic. Future Director output may add a validated preference, but unsupported free-form model names must never reach the dispatcher.
- Any future image backend must have an explicit dispatcher branch, schema enum, preflight, focused tests, and one real acceptance record before appearing in UI.
- Do not equate unit coverage with production reachability. Tests must invoke the real production dispatcher rather than simulate copied logic.
- Preserve the low-resource default: DreamShaper/Qwen sequentially, not concurrently, followed by Supertonic and FFmpeg.

