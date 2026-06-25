# Video.AI Current-Code Audit - 2026-06-16

Repository root: `C:\Video.AI`

Current commit checked: `9836596a522728263e02af09e232705a6feee057`

This report is based on the current local checkout, not the older reviewed
commit in the pasted audit. I scanned tracked Python, Rust, and dashboard source,
then ran targeted tests and lightweight reproductions for the risky paths.

## Verification Performed

- Source inventory: `rg --files`, `git status --short`
- Static scans:
  - unsafe execution/deserialization patterns: `eval`, `exec`, `pickle`, `yaml.load`, `shell=True`, `os.system`
  - auth/exposure-sensitive fields: `request_json`, `error`, tokens, CORS, bind hosts
  - path handling and filesystem operations
  - prompt-template formatting hazards
  - ComfyUI URL construction
- Python parse check:
  - `python -m compileall -q agents audio config core jobs memory scripts utils video bootstrap_pipeline.py run_pipeline.py studio_tui.py studio_tui_helpers.py style_resolver.py setup_youtube_profile.py`
- Targeted Python tests:
  - `python -m pytest tests/test_preflight_extended.py tests/test_layered_v3.py tests/test_assembler_extra.py tests/test_project_store.py tests/test_world_state.py tests/test_director_agent_extended.py tests/test_pre_production_extended.py tests/test_job_system.py tests/test_local_ui_api.py -q`
  - Result: `208 passed, 3 skipped`
- Rust tests:
  - `cargo test --manifest-path rust\worker\Cargo.toml`
  - Result: exit code 0
- Python lint:
  - `python -m ruff check . --statistics`
  - Result: exit code 0
- Dashboard checks:
  - `npm run lint`
  - `npm run test:run`
  - Result: `20 passed` test files, `167 passed` tests

## Confirmed Findings

### 1. Upload-script job route bypasses request validation

Severity: Medium

Location:
- `utils/local_ui.py:106`
- `utils/local_ui.py:287`
- `utils/local_ui.py:341`
- `utils/local_ui.py:379`
- `utils/local_ui.py:389`

Problem:
`/api/jobs` calls `_validate_job_request()` before queuing a job, but
`/api/upload_script` builds the same kind of job payload and never calls that
validator. It accepts invalid `run_mode` values and silently coerces invalid
boolean form fields.

Reproduction:
Using `fastapi.testclient.TestClient`, posting to `/api/upload_script` with
`run_mode='../../bad'` returned HTTP 200 and queued this payload:

```text
{'topic': 'T', 'content_text': 'hello story', 'dry_run': False,
 'no_resume': True, 'skip_rvc': True, 'series': False,
 'director_mode': False, 'run_mode': '../../bad',
 'image_backend': 'comfyui', 'comfyui_checkpoint': 'DreamShaper_8.safetensors'}
```

Impact:
Invalid or unexpected job options can enter the queue through the form upload
path while the JSON API rejects them. Today `argparse` will reject invalid
`--run-mode` later, but the job is already persisted and fails downstream
instead of being rejected at the API boundary.

Recommended fix:
Call `_validate_job_request(job_request)` in `/api/upload_script` before
`job_store.create_job()`. Consider making form boolean parsing strict so
invalid text such as `not-a-bool` returns 400 instead of silently becoming
False.

### 2. Project config name allows path traversal outside `projects/`

Severity: Medium

Location:
- `config/config.py:45`
- `bootstrap_pipeline.py:411`
- `bootstrap_pipeline.py:464`
- `core/pipeline_long.py:170`

Problem:
`load_config(project_name=...)` constructs the project config path with:

```python
Path("projects") / f"{project_name}.yaml"
```

The raw `project_name` is not sanitized or confined. A value such as
`../config/config` resolves to `projects/../config/config.yaml`, which exists
and is loaded. A quick local check confirmed:

```text
load_config(project_name='../config/config') -> loaded traversal project config
```

Impact:
Any caller that accepts `--project` or a queued `project` field can cause the
config loader to read YAML files outside `projects/`, as long as the path ends
in `.yaml`. This is local-tooling scoped, but it breaks the expected project
boundary and can lead to surprising config overlays.

Recommended fix:
Sanitize `project_name` with the existing safe filename policy, reject path
separators and `..`, then resolve and require the final path to stay under
`projects/`.

### 3. `analyze_story` prompt key is missing, so the YAML vision prompt is not used

Severity: Medium

Location:
- `agents/director_agent.py:864`
- `prompts.yaml:15`

Problem:
`DirectorAgent.analyze_with_research()` asks for `_prompt("analyze_story", ...)`,
but `prompts.yaml` does not define `analyze_story`; it defines
`vision_document`. A tracked-source AST check found:

```text
used _prompt keys: analyze_story, consultation_questionnaire,
custom_instructions_options, invent_story, writer_breakdown
missing prompt keys used: analyze_story
unused prompt keys: cliffhanger_detection, define_pacing, extract_characters,
generate_hinglish, read_story, story_compaction, vision_document
```

Impact:
The tuned `vision_document` template in `prompts.yaml` is dead for the current
analysis path. The code always falls back to the shorter inline prompt, so
changes to `vision_document` do not affect production behavior.

Recommended fix:
Either rename `vision_document` to `analyze_story`, or change
`analyze_with_research()` to request `vision_document`. Add a test that every
`_prompt("...")` key exists in `prompts.yaml`.

### 4. Two unused YAML prompt templates fail direct `.format()`

Severity: Low

Location:
- `prompts.yaml:62`
- `prompts.yaml:226`

Problem:
The `vision_document` and `cliffhanger_detection` YAML templates contain
single-brace JSON examples. Direct `.format()` raises:

```text
vision_document KeyError '\n  "characters"'
cliffhanger_detection KeyError '"point"'
story_compaction OK
```

In current code these two YAML keys appear unused by `_prompt()` call sites,
which lowers the severity. If the missing prompt-key issue above is fixed by
using `vision_document`, this becomes an active bug immediately.

Recommended fix:
Escape literal JSON braces in those YAML templates by doubling them, and add a
prompt-format smoke test that formats all YAML prompt templates with dummy
values.

### 5. Config default selects NVENC, but encoder fallback is bypassed

Severity: High on non-NVIDIA machines; Medium otherwise

Location:
- `config/config.yaml:52`
- `video/renderer/assembler.py:90`
- `video/renderer/assembler.py:138`
- `video/renderer/assembler.py:150`

Problem:
The default config sets `video.encoder: h264_nvenc`. `_encoder_args()` returns
NVENC arguments directly when that value is selected. The capability-detecting
fallback in `_get_video_codec()` only runs for non-`h264_nvenc` values.

Impact:
On systems without NVENC support, ffmpeg render can fail instead of falling
back to `libx264`. Existing tests currently assert the direct NVENC behavior,
so the tests encode the risky behavior rather than protecting the fallback.

Recommended fix:
Route the `h264_nvenc` case through a capability check, or make `_encoder_args()`
fall back to `libx264` when ffmpeg does not list `h264_nvenc`. Update
`tests/test_assembler_extra.py::test_encoder_args` accordingly.

### 6. Whisper model cache ignores final vs preview model choice

Severity: Medium

Location:
- `video/renderer/assembler.py:21`
- `video/renderer/assembler.py:29`
- `video/renderer/assembler.py:39`
- `video/renderer/assembler.py:44`
- `config/config.yaml:209`
- `config/config.yaml:210`

Problem:
`_get_whisper_model(is_final)` chooses `whisper_model_final` for final renders
and `whisper_model` for preview/fallback work, but the cache is a single global
`_whisper_model`. Once any model is loaded, later calls return it regardless of
`is_final` or model name.

Impact:
If a preview path loads `tiny` first, the later final path can reuse `tiny`
instead of `base`. If final loads first, preview can reuse the heavier model.

Recommended fix:
Cache by `(backend, model_name, device, compute_type)` or at least by
`model_name`.

### 7. Job status endpoints expose raw job payloads and errors without auth

Severity: Medium

Location:
- `rust/worker/src/status.rs:51`
- `rust/worker/src/status.rs:55`
- `rust/worker/src/status.rs:65`
- `rust/worker/src/status.rs:123`
- `rust/worker/src/status.rs:128`
- `rust/worker/src/status.rs:129`
- `rust/worker/src/main.rs:98`

Problem:
The Rust status server exposes `/jobs` and `/jobs/:id` with no authentication
and serializes `request_json` and `error`. The default bind host is
`127.0.0.1`, which limits default exposure, but the CLI allows other bind
hosts.

Impact:
Anyone who can reach the status port can read full queued job payloads and
error detail. This may include source text or operator-provided job fields.

Recommended fix:
Keep localhost as the default, add an explicit warning or refusal for non-local
binds unless a token is configured, and redact or omit `request_json` from list
responses.

### 8. Local UI serves full runtime output and project directories

Severity: Medium

Location:
- `utils/local_ui.py:200`
- `utils/local_ui.py:205`

Problem:
The FastAPI app statically mounts `studio_outputs` and `studio_projects`. The
app is intended to be local-only and CORS is restricted to local dashboard
origins, but static file routes themselves are not authenticated.

Impact:
If the UI server is bound beyond localhost by a launcher or deployment wrapper,
generated videos, manifests, project metadata, and character assets become
directly browsable.

Recommended fix:
Enforce localhost binding in the server launcher by default and document it.
For any non-local mode, put static file access behind authentication or serve
only whitelisted artifact files.

### 9. WorldState regex fallback over-collects character names

Severity: Low to Medium

Location:
- `memory/memory.py:233`
- `memory/memory.py:272`

Problem:
The regex fallback treats many capitalized words and arbitrary Devanagari runs
as character candidates. The LLM extraction path is enabled by default, but the
fallback still runs on LLM failure.

Impact:
World-state character memory can become polluted, especially on non-English or
mixed-script narration.

Recommended fix:
Prefer plan-provided character names, require repeated mentions or nearby
speaker/character cues, or make regex extraction opt-in.

### 10. WorldState facts cap can exceed 30 after key event append

Severity: Low

Location:
- `memory/memory.py:310`
- `memory/memory.py:322`

Problem:
`world_facts` is truncated to the last 30 items, then `key_event` is appended
afterward. The list can hold 31 items.

Impact:
Minor cap drift and slightly larger prompt memory than intended.

Recommended fix:
Append `key_event` before the final truncation, or truncate again after append.

### 11. Continuity audit only checks two hardcoded visual contradictions

Severity: Low to Medium

Location:
- `memory/project_store.py:620`
- `memory/project_store.py:638`
- `memory/project_store.py:642`

Problem:
`check_continuity()` only detects `blue eyes` vs `red eyes` and `black hair`
vs `blonde hair`, and only when the character first name appears in the target.

Impact:
Most continuity drift is not detected, especially clothing, scars, age, props,
relationships, non-English descriptors, and other hair/eye colors.

Recommended fix:
Extract structured character facts and compare against normalized attributes
instead of hardcoded English string pairs.

### 12. FFmpeg concat manifest does not escape single quotes in paths

Severity: Low

Location:
- `video/renderer/assembler.py:536`

Problem:
The concat list writes paths as:

```python
file '{p.absolute().as_posix()}'
```

Single quotes inside a path are not escaped.

Impact:
Current generated temp paths are unlikely to contain quotes, but a quoted
workspace/output path would break final concatenation.

Recommended fix:
Escape single quotes according to ffmpeg concat demuxer rules or use a helper
that serializes concat file entries safely.

### 13. Subtitle language default conflicts with Hindi TTS default

Severity: Low

Location:
- `config/config.yaml:201`
- `config/config.yaml:209`
- `video/renderer/assembler.py:227`
- `video/renderer/assembler.py:791`

Problem:
The config defaults subtitles to `language: en`, while the rest of the pipeline
is configured for Hindi/Devanagari narration. The assembler treats non-`auto`
language as a translation request and has special handling for English
subtitles over Devanagari script.

Impact:
Subtitles can be translated or generated differently from the spoken narration
by default, which may surprise operators expecting same-language captions.

Recommended fix:
Default subtitles to `auto` or derive the value from `tts.lang` / top-level
language unless the operator explicitly requests translation.

## Stale Or Not Confirmed From The Pasted Report

- The systemic Python malformed ComfyUI URL bug is fixed in this checkout.
  The named Python files now construct URLs like `http://{host}:{port}`.
- The Rust worker still contains `{{http://...` only in an error message at
  `rust/worker/src/main.rs:840`; the actual socket request uses host and port
  separately.
- `story_compaction` formats cleanly and the current `compact_story()` method
  uses an inline f-string prompt rather than the YAML template.
- `vision_document` and `cliffhanger_detection` YAML templates are broken under
  `.format()`, but current tracked `_prompt()` call sites do not use those keys.

## Residual Risk

I did not run a full end-to-end generation with ComfyUI, ffmpeg rendering, TTS,
or Ollama models. The tests and scans above verify source behavior and many
unit/integration slices, but hardware/backend runtime failures still need a
real pipeline run.
