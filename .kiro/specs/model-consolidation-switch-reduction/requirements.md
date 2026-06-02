# Requirements Document

## Introduction

Video.AI runs every large language model (LLM) task through a local Ollama server on an RTX 4050 laptop GPU that has only 6 GB of video memory (VRAM). Because only one model fits in 6 GB at a time, the pipeline (`core/pipeline_long.py`) currently maintains several separate text-role Ollama models — Director, Writer, Reviewer, and Image Engineer — and Ollama must evict the previous model before the next one loads. Each eviction-and-reload costs wall-clock time and adds operational complexity, and these costs repeat across every segment of a long video. This is the operator's primary pain point: model switching is slow and error-prone.

This feature reduces the time and complexity caused by model switching in two complementary ways:

1. **Consolidation** — replace the four separate text-role Ollama models (Director, Writer, Reviewer, Image Engineer) with a single dense 7–8B instruct model that fits in 6 GB, and switch between roles by changing the system prompt instead of swapping models.
2. **Staged batching** — implement a true staged loop that runs all text work for a batch of segments while the single text model stays resident, then evicts once, loads Stable Diffusion (SD) once for the whole batch, then runs text-to-speech (TTS).

The Hindi/Devanagari translator (Sarvam) stays a separate model so translation quality does not regress. The TTS engine and the Stable Diffusion image model are not LLMs and also stay separate; on 6 GB they must take the GPU on their own. The change is config-driven, preserves the existing single-model-at-a-time safety, preserves checkpoint/resume, preserves graceful fallbacks, must not regress the Director's structured-JSON planning, and provides a measurable way to confirm the reduction in model loads.

## Glossary

- **Pipeline**: The orchestration code in `core/pipeline_long.py` that produces a narrated, subtitled video segment by segment.
- **Text_LLM**: The single consolidated dense instruct model (for example Qwen2.5-7B-Instruct) configured to perform all text-generation roles via system-prompt switching.
- **Text_Role**: One of the text-generation functions previously served by a separate model: Director (planning), Writer (prose), Reviewer (script review), Image_Engineer (Stable Diffusion prompt writing).
- **Role_Resolver**: The component that selects the correct system prompt and generation parameters for a given Text_Role and submits the request to the Text_LLM.
- **Translator**: The separate Sarvam Ollama model used for Hindi/Devanagari translation, configured at `models.translator`.
- **TTS_Engine**: The text-to-speech component (OmniVoice or edge-tts); not an LLM.
- **SD_Engine**: The local Stable Diffusion image generation component; not an LLM.
- **VRAM_Manager**: The `_evict_ollama_models()` function and its VRAM polling logic that force-evict Ollama models (keep_alive=0) and verify free VRAM before SD or TTS use the GPU.
- **Staged_Loop**: The batched execution mode controlled by `performance.staged_loop` that runs the text phase for a batch of segments, then a single eviction, then the GPU phase.
- **Batch**: A group of `performance.lookahead_segments` consecutive segments processed together in the Staged_Loop.
- **Checkpoint_Manager**: The resume mechanism (`checkpoint.enabled`, `studio_checkpoints/`) that lets a long run resume after a crash.
- **Config_Loader**: `config/config.py` `load_config`, which deep-merges defaults with `config/config.yaml` and validates against the schema in `config/config_schema.py`.
- **Load_Metrics_Recorder**: The component that records the count and duration of model loads/evictions per run for verification.
- **Operator**: The single human user running the pipeline on their own Windows machine.
- **6GB_Budget**: The constraint that no more than one Ollama model and one GPU consumer (SD or TTS) may hold VRAM at the same time on the 6 GB GPU.

## Requirements

### Requirement 1: Consolidated text model that fits the 6 GB budget

**User Story:** As an operator on a 6 GB GPU, I want all text-generation roles served by one dense model that fits in VRAM, so that the pipeline maintains fewer models and reloads them less often.

#### Acceptance Criteria

1. THE Config_Loader SHALL read a single consolidated text model name from a configuration key under `models`.
2. WHERE a consolidated text model is configured, THE Pipeline SHALL route the Director, Writer, Reviewer, and Image_Engineer Text_Roles to that single Text_LLM.
3. THE Text_LLM SHALL be a dense (non-mixture-of-experts) instruct model whose quantized resident footprint fits within the 6GB_Budget while leaving at least `performance.vram_sd_threshold_gb` of VRAM free after eviction.
4. WHILE the Text_LLM is performing any Text_Role, THE Pipeline SHALL keep at most one Ollama model resident in VRAM.
5. WHERE the consolidated text model is not configured, THE Pipeline SHALL use the existing per-role model names so that current behavior is preserved.

### Requirement 2: Role switching by system prompt

**User Story:** As an operator, I want the pipeline to switch text roles by changing the system prompt rather than swapping models, so that role changes add no model-reload time.

#### Acceptance Criteria

1. WHEN the Pipeline requests a specific Text_Role from the Text_LLM, THE Role_Resolver SHALL select the system prompt and generation parameters defined for that Text_Role.
2. WHILE the consolidated text model is active, WHEN the Pipeline changes from one Text_Role to another Text_Role, THE Pipeline SHALL submit the new request to the resident Text_LLM without evicting or reloading the model.
3. THE Role_Resolver SHALL produce, for each Text_Role, output that satisfies the same downstream contract that the corresponding separate model satisfied (planning JSON for Director, prose for Writer, review JSON for Reviewer, prompt text for Image_Engineer).
4. WHERE per-role generation parameters (temperature, JSON formatting, token limit) are configured, THE Role_Resolver SHALL apply those parameters for the requested Text_Role.

### Requirement 3: Director structured-JSON reliability

**User Story:** As an operator, I want the Director's planning output to remain reliable structured JSON, so that story planning does not break when roles share one model.

#### Acceptance Criteria

1. WHEN the Pipeline requests the Director Text_Role, THE Role_Resolver SHALL request JSON-formatted output from the Text_LLM.
2. WHEN the Director Text_Role returns a response, THE Pipeline SHALL parse the response into the planning object using the existing brace-depth JSON extraction.
3. IF the Director response cannot be parsed into valid JSON, THEN THE Pipeline SHALL apply the existing retry-and-fallback handling without aborting the run.
4. THE Director Text_Role JSON parse success rate over a benchmark set SHALL be greater than or equal to the success rate recorded for the current separate Director model on the same benchmark set.

### Requirement 4: Translator remains a separate model

**User Story:** As an operator producing Hindi narration, I want the Sarvam translator kept as its own model, so that Devanagari translation quality does not regress.

#### Acceptance Criteria

1. THE Pipeline SHALL keep the Translator as a separate Ollama model configured at `models.translator`.
2. WHEN translation to Hindi/Devanagari is required, THE Pipeline SHALL route the request to the Translator and not to the Text_LLM.
3. THE consolidation of Text_Roles into the Text_LLM SHALL NOT change the model used for translation.
4. WHILE the Translator is loaded for a translation task, THE VRAM_Manager SHALL enforce the 6GB_Budget so that the Text_LLM and the Translator are not co-resident.

### Requirement 5: TTS and Stable Diffusion remain separate GPU consumers

**User Story:** As an operator, I want voice synthesis and image generation to keep their own engines, so that the consolidation does not change audio or image quality.

#### Acceptance Criteria

1. THE Pipeline SHALL continue to use the configured TTS_Engine for voice synthesis and the configured SD_Engine for image generation.
2. THE consolidation of Text_Roles SHALL NOT change the TTS_Engine or the SD_Engine.
3. WHEN the SD_Engine or the TTS_Engine requires the GPU, THE VRAM_Manager SHALL evict all resident Ollama models before the GPU consumer loads.
4. WHEN the VRAM_Manager evicts before a GPU task, THE VRAM_Manager SHALL poll free VRAM until it reaches `performance.vram_sd_threshold_gb` or until `performance.vram_evict_wait_s` elapses.

### Requirement 6: Single-model-at-a-time VRAM safety preserved

**User Story:** As an operator on a constrained GPU, I want the one-model-at-a-time rule preserved, so that the GPU never deadlocks from VRAM overcommit.

#### Acceptance Criteria

1. THE Pipeline SHALL keep `performance.max_workers` at 1 so that segments run serially.
2. WHILE any GPU consumer (Text_LLM, Translator, SD_Engine, or TTS_Engine) holds VRAM, THE Pipeline SHALL NOT load a second VRAM-holding consumer.
3. WHEN the VRAM_Manager force-evicts Ollama models, THE VRAM_Manager SHALL include the consolidated text model name among the models it evicts.
4. IF free VRAM remains below `performance.vram_sd_threshold_gb` after `performance.vram_evict_wait_s`, THEN THE VRAM_Manager SHALL log a warning and continue without crashing the run.

### Requirement 7: True staged and batched execution loop

**User Story:** As an operator, I want all text work for a batch of segments done before the GPU phase, so that the pipeline evicts and reloads SD and TTS far less often.

#### Acceptance Criteria

1. WHERE `performance.staged_loop` is true, THE Staged_Loop SHALL run the text phase (script generation, review, translation, image-prompt generation) for all segments in a Batch while the Text_LLM stays resident, before any eviction for that Batch.
2. WHERE `performance.staged_loop` is true, WHEN the text phase for a Batch completes, THE Staged_Loop SHALL evict Ollama models exactly once before starting the GPU phase for that Batch.
3. WHERE `performance.staged_loop` is true, THE Staged_Loop SHALL set the Batch size from `performance.lookahead_segments`.
4. WHILE the Staged_Loop runs, THE Pipeline SHALL keep at most one model resident at any time, honoring the 6GB_Budget.
5. WHERE `performance.staged_loop` is false, THE Pipeline SHALL execute segments in the current per-segment order so that existing behavior is preserved.
6. THE Staged_Loop SHALL preserve the adjacency of TTS and subtitle-timing steps so that subtitle timing does not desynchronize.

### Requirement 8: Config-driven and schema-validated

**User Story:** As an operator, I want the consolidation and batching controlled from config, so that I can tune or disable them without code changes.

#### Acceptance Criteria

1. THE Config_Loader SHALL load the consolidated text model name, the per-role prompt and parameter settings, `performance.staged_loop`, and `performance.lookahead_segments` from `config/config.yaml`.
2. THE Pipeline SHALL read these settings through the loaded configuration dictionary rather than from hardcoded values.
3. WHERE a new configuration key is absent, THE Config_Loader SHALL apply a default value that preserves current behavior.
4. WHEN the configuration is validated, THE schema in `config/config_schema.py` SHALL accept the new keys without rejecting the configuration.

### Requirement 9: Checkpoint and resume preserved

**User Story:** As an operator running multi-hour videos, I want checkpointing to keep working with the staged loop, so that a crash still resumes from the last completed work.

#### Acceptance Criteria

1. WHILE the Staged_Loop runs, THE Pipeline SHALL write a checkpoint after each completed sub-step using additive checkpoint keys.
2. WHEN a run resumes from a checkpoint, THE Pipeline SHALL continue from the last completed segment or sub-step without repeating completed GPU work.
3. THE staged batching SHALL store per-segment text outputs (scripts, translations, image prompts) to checkpoints before the first eviction of a Batch so that resume does not require regenerating that text.
4. WHERE `checkpoint.enabled` is true, THE consolidation and Staged_Loop changes SHALL NOT reduce the set of recoverable run state compared to current behavior.

### Requirement 10: Graceful fallback when the consolidated model fails

**User Story:** As an operator, I want a safe fallback if the consolidated model fails, so that a single bad response does not abort a long run.

#### Acceptance Criteria

1. IF a Text_Role request to the Text_LLM fails or times out, THEN THE Pipeline SHALL apply the existing per-request retry policy bounded by `ollama.request_timeout` and the circuit-breaker settings.
2. IF a Text_Role continues to fail after its retry budget is exhausted, THEN THE Pipeline SHALL record a degradation entry and continue the run using the existing safe fallback for that role.
3. WHERE a per-role fallback model is configured, IF the Text_LLM is unavailable, THEN THE Pipeline SHALL route the affected Text_Role to the configured fallback model.
4. WHEN a fallback path is taken, THE Pipeline SHALL keep the 6GB_Budget by evicting before loading any fallback model.

### Requirement 11: Measurable reduction in model loads

**User Story:** As an operator, I want to measure model loads per run, so that I can verify the switching cost actually dropped.

#### Acceptance Criteria

1. WHEN a run completes, THE Load_Metrics_Recorder SHALL record the count of model loads and the count of evictions performed during the run.
2. WHEN a run completes, THE Load_Metrics_Recorder SHALL record the total time spent loading and evicting models during the run.
3. THE Load_Metrics_Recorder SHALL write these metrics to the run manifest so that the Operator can compare runs.
4. WHEN the consolidated text model and the Staged_Loop are both enabled, THE recorded count of text-model loads per run SHALL be lower than the count recorded for the current separate-model, non-staged configuration on an equivalent run.

### Requirement 12: Backward compatibility and rollback

**User Story:** As an operator, I want to turn the new behavior off, so that I can fall back to the known-good configuration if needed.

#### Acceptance Criteria

1. WHERE the consolidated text model is not configured and `performance.staged_loop` is false, THE Pipeline SHALL behave exactly as the current pipeline behaves.
2. THE Pipeline SHALL keep the existing command-line interface (`bootstrap_pipeline.py`) and the FastAPI `/api/status` response shape unchanged.
3. WHEN the Operator sets `performance.staged_loop` to false, THE Pipeline SHALL revert to the current per-segment execution order.
4. THE feature SHALL NOT introduce a mandatory new network call, cloud-inference dependency, or required hardware beyond the current local 6 GB GPU setup.
