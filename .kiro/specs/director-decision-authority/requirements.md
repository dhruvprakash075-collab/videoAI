# Requirements Document

## Introduction

The Director agent is the creative authority of the Video.AI pipeline. It analyzes a
topic or story, optionally researches the web, consults the Writer, and (when needed)
asks the user, then produces a runtime configuration that drives the rest of the
pipeline (TTS, image generation, rendering).

Today there are **two divergent Director flows** and the runtime **does not fully
respect the Director's structural decisions**:

- The **CLI / batch path** (`core/pipeline_long.py` → `run_pre_production`) uses
  `analyze_with_research → consult_on_config → consult_with_writer →
  produce_runtime_config`.
- The **dashboard / API path** (`utils/local_ui.py` → `run_pipeline_thread`) uses a
  separate, lighter flow: `read_story → define_pacing_and_length →
  generate_hinglish_script`, and never calls `run_long_pipeline`.

Confirmed problems this spec addresses:

1. **Segment count is ignored at runtime.** `produce_runtime_config` computes a
   Director/Writer-agreed `segment_count`, but `pipeline_long.py` re-derives
   `n_segs = ceil(total_duration_min / segment_duration_min)`, so the agreed segment
   count is dropped.
2. **Per-segment word count authority is split.** The Director sets
   `words_per_segment`, but the actual value used per segment is
   `plan.target_word_count` from a *separate* LLM call in `utils/story_planner.py`;
   the Director's value is only a fallback. The two are never reconciled.
3. **User override is "soft".** User intervention is matched by fuzzy string
   checks (e.g. `"tiktok" in reply.lower()`) and structural fields (word count,
   segment count, duration) cannot be hard-locked by the user.
4. **Two parallel decision implementations** (`define_pacing_and_length` for the
   dashboard vs. the full CLI chain) drift independently.
5. **A validated schema already exists but is unused.** `config/config_schemas.py`
   (`VisionDocument`, `WriterBreakdown`, `ConfigOverlay`, with range clamps and a
   prompt-injection sanitizer) models exactly these decisions but is never used; the
   Director re-implements clamping inline.
6. **Duration negotiation is dormant for `--topic` runs.** `consult_on_duration`,
   `suggest_cliffhangers`, and `compact_story` only run on the `content_text`
   (file-upload) path.
7. **Cliffhanger and compaction are stubs.** `suggest_cliffhangers()` returns `[]`
   and `compact_story()` returns the text unchanged, so the user's two key
   length-control options are not actually implemented.
8. **Project handling is dead-wired.** `run_long_pipeline` accepts `project_name` but
   calls `load_config()` without it, so `projects/{name}.yaml` overrides never load.
   Per-story memory exists (`studio_checkpoints/{topic}_memory.json`) but is flat and
   keyed only by topic — there is no per-project folder grouping multiple stories for
   continuity.

This spec unifies the two flows into a single authoritative structural-decision
system with an explicit authority model: **Director proposes → Writer consents →
User can hard-override (strict rules) → runtime obeys.**

## Glossary

- **Structural decision**: any value that determines the shape/length of the output:
  `total_duration_min`, `segment_count`, `segment_duration_min`, `words_per_segment`
  (global and per-segment `target_word_count`), `images_per_segment` / `num_images`.
- **Decision record**: the single validated object produced by the Director that
  carries every structural decision plus its provenance (who set it) and lock state.
- **Hard-override / lock**: a user-specified value that downstream stages
  (Director merge, Writer proposal, story planner, pipeline) MUST NOT change.
- **Provenance**: the origin of a decision value — one of `default`, `director`,
  `writer`, `user`, `cli_flag`.
- **Project**: a named, persistent container grouping one or more stories that share
  continuity (characters, world, motifs, decision history).
- **One-time-use run**: an ephemeral run whose artifacts and memory do not persist into
  any project's continuity store.
- **Cliffhanger point**: a Director-proposed end point where the story ends on a high
  note, used to cap a long story into a satisfying shorter video.
- **Compaction**: condensing the source story so the produced video fits a
  user-specified time limit.
- **Blackboard**: a shared, structured workspace (on disk/RAM) that all agents read
  from and write to, instead of one agent handing values down to the next
  (master–slave). Here it comprises the decision record plus project/story memory.
- **Risk tier**: a classification of each decision by impact — high-impact structural
  decisions require a user gate; low-impact cosmetic decisions auto-proceed.
- **Visual lock**: a stored per-character reference (description and/or seed/LoRA) used
  to keep a character's appearance stable across many image generations.
- **CLI / batch path**: `core/pipeline_long.py` → `run_pre_production` →
  `run_long_pipeline`.
- **Dashboard / API path**: `utils/local_ui.py` → `run_pipeline_thread`.

## Requirements

### Requirement 1: Single authoritative decision record

**User Story:** As an operator, I want all structural decisions to live in one
validated object so that there is a single source of truth the whole pipeline obeys.

#### Acceptance Criteria

1. WHEN the Director completes pre-production THEN the system SHALL produce a single
   decision record containing at least `total_duration_min`, `segment_count`,
   `segment_duration_min`, `words_per_segment`, `images_per_segment`, and per-segment
   overrides where applicable.
2. WHERE a value is set, the decision record SHALL record its provenance
   (`default` | `director` | `writer` | `user` | `cli_flag`).
3. The decision record SHALL be validated against a schema that clamps values to safe
   ranges (e.g. `words_per_segment` 50–800, `segment_count` 1–N, `images_per_segment`
   1–30) and SHALL reuse the existing `config/config_schemas.py` models rather than
   re-implementing clamping inline.
4. IF validation fails for any field THEN the system SHALL fall back to a documented
   default for that field, log a warning, and continue (no hard crash).
5. The decision record SHALL be serialized into the config overlay and persisted so a
   resumed or series run reuses the same decisions.

### Requirement 2: Director proposes, Writer consents

**User Story:** As the Director, I want to propose structural decisions and have the
Writer review/adjust them so that the plan reflects both creative vision and
screencraft.

#### Acceptance Criteria

1. WHEN analysis completes THEN the Director SHALL propose initial structural values
   (duration, segment count, words/segment, images/segment) derived from content
   analysis.
2. WHEN the Director has proposed values THEN the Writer SHALL be consulted and MAY
   adjust any non-locked structural value, returning its rationale.
3. WHERE the Writer's adjustment falls outside the valid range, the system SHALL clamp
   it and record the clamp.
4. IF the Writer is unavailable or returns an invalid response THEN the system SHALL
   keep the Director's proposed values and log the fallback.
5. The final agreed values SHALL be recorded with provenance `writer` when the Writer
   changed them, otherwise `director`.

### Requirement 3: User hard-override (strict rules)

**User Story:** As an operator, I want to impose strict rules (e.g. "exactly 12
segments", "180 words per segment") that nothing downstream may change.

#### Acceptance Criteria

1. WHERE the user supplies a structural value (via CLI flag, config, or consultation),
   the system SHALL mark that field as locked with provenance `user`.
2. WHEN a field is locked THEN the Director merge, the Writer proposal, the story
   planner, and the pipeline SHALL NOT override it.
3. IF a locked value conflicts with another locked value (e.g. duration vs. segment
   count × segment duration) THEN the system SHALL surface the conflict to the user
   and SHALL NOT silently pick one.
4. WHEN no user value is provided for a field THEN the field SHALL remain
   Director/Writer-controlled.
5. The user override mechanism SHALL be explicit (typed/structured input), not
   inferred via substring matching of free text.
6. User-supplied free-text instructions SHALL be passed through the existing
   prompt-injection sanitizer before use.

### Requirement 4: Runtime obeys the decision record

**User Story:** As an operator, I want the pipeline to actually use the agreed
structural decisions so that the output matches what was planned.

#### Acceptance Criteria

1. WHEN the pipeline computes segment count THEN it SHALL use the decision record's
   `segment_count` and SHALL NOT silently re-derive it from
   `total_duration_min / segment_duration_min`.
2. IF `segment_count` and duration are inconsistent THEN the system SHALL reconcile
   them according to the lock/provenance rules in Requirement 3 and log the resolution.
3. WHEN the story planner assigns per-segment `target_word_count` THEN it SHALL treat
   the decision record's `words_per_segment` as the authoritative target/average and
   SHALL keep per-segment variation within a documented band around it.
4. WHERE a per-segment value is user-locked THEN the planner SHALL emit exactly that
   value.
5. The pipeline's segment-breakdown log SHALL show the source (provenance) of the
   segment count and word targets it used.

### Requirement 5: Unify the two Director flows

**User Story:** As a developer, I want one Director decision pathway used by both the
CLI and the dashboard so that behavior cannot drift between them.

#### Acceptance Criteria

1. WHEN the dashboard starts a run THEN it SHALL use the same structural-decision
   pathway as the CLI pipeline (the unified pre-production + decision record), rather
   than a separate `read_story → define_pacing_and_length` path.
2. The unified pathway SHALL support both entry inputs: a topic string (`--topic`) and
   a story file / uploaded text (`--file` / dashboard upload).
3. WHERE the dashboard previously called `define_pacing_and_length` /
   `generate_hinglish_script`, those responsibilities SHALL be served by the unified
   pathway, and any now-redundant method SHALL be removed or reduced to a thin shim
   that delegates to it.
4. The unified pathway SHALL preserve existing UI behavior: live status/logging,
   human-in-the-loop pause/resume, and the consultation prompts.
5. Both entry points SHALL produce an identical decision-record structure for
   equivalent inputs.

### Requirement 6: Director-chosen video length with user length-control

**User Story:** As an operator, I want the Director to choose the video length from the
script, but I want to be able to step in and either cap the length or pick where the
video ends on a high note.

#### Acceptance Criteria

1. WHEN the Director analyzes the source THEN it SHALL choose a recommended
   `total_duration_min` based on the script's content (length, complexity, number of
   arcs, pacing, natural break points) and record it with provenance `director`.
2. WHEN a run starts from `--topic` (no source text) THEN duration negotiation SHALL
   still be available using the Director's estimated duration.
3. WHEN a run starts from a story file THEN duration negotiation SHALL estimate from
   content and offer the user length-control options.
4. WHERE the user chooses to condense, the user SHALL be able to specify a target time
   limit, AND the Director SHALL compact the story to fit that limit (this replaces the
   current `compact_story` stub with a real implementation).
5. WHERE the user chooses cliffhangers, the Director SHALL propose two or more
   candidate end points where the story ends on a high note, AND the user SHALL pick
   one; the video SHALL end at the chosen point (this replaces the current
   `suggest_cliffhangers` stub with a real implementation).
6. WHERE the user has locked the duration (Requirement 3) THEN duration negotiation
   SHALL be skipped and the locked value used.
7. IF the user declines to negotiate (timeout / non-interactive) THEN the system SHALL
   use the Director's recommended duration and log the choice.
8. WHEN the user condenses or selects a cliffhanger THEN the resulting duration and
   end point SHALL be written into the decision record with provenance `user`.

### Requirement 7: Multi-story project continuity

**User Story:** As an operator, I want each source I give the Director to be stored
under its own project so that continuity (characters, world, recurring motifs) is
maintained across multiple stories in the same project.

#### Acceptance Criteria

1. WHEN the user provides source material THEN the system SHALL store it under a
   per-project location (e.g. a project folder) rather than a single flat,
   topic-keyed file.
2. WHEN a project already exists THEN a new story added to it SHALL have access to the
   project's accumulated continuity (characters, motifs, world facts, prior decision
   records).
3. WHERE `--project {name}` is supplied THEN `projects/{name}.yaml` overrides SHALL
   actually be loaded (fixing the current dead-wiring where `project_name` is never
   passed to `load_config`).
4. The system SHALL keep per-story memory and per-project memory distinct: a story's
   own segments/state vs. the shared project continuity it draws from.
5. WHEN a series/project run resumes THEN it SHALL reuse the project's stored decision
   records and continuity without re-prompting unless asked.
6. The project store SHALL be addressable so multiple projects can coexist and a user
   can run different projects independently.

### Requirement 8: Project vs. one-time-use mode (backend)

**User Story:** As an operator, I want to declare at the start whether a run is part of
a named project or a one-time throwaway, so that continuity is only persisted when I
want it.

#### Acceptance Criteria

1. WHEN a run starts THEN the system SHALL accept a backend-level indication of mode:
   a named project (persistent) or one-time use (ephemeral).
2. WHERE the mode is a named project THEN source material, memory, and decision
   records SHALL be persisted under that project for future continuity.
3. WHERE the mode is one-time use THEN the run SHALL NOT pollute any project's
   persistent continuity store, and its artifacts SHALL be isolated.
4. The mode selection SHALL be a backend capability (CLI flag / API field); the
   front-end presentation of this choice is out of scope for this spec.
5. IF no mode is specified THEN the system SHALL default to a documented behavior
   (e.g. one-time use) and log the chosen default.

### Requirement 9: Backward compatibility and safety

**User Story:** As an operator with existing runs and configs, I want the change to not
break my current setup.

#### Acceptance Criteria

1. WHEN an existing `config.yaml` (without a decision record) is loaded THEN the system
   SHALL construct a decision record from existing config values with provenance
   `default`/`cli_flag` and continue normally.
2. WHEN resuming from an existing checkpoint THEN the system SHALL honor previously
   persisted decisions and SHALL NOT re-prompt the user unless asked.
3. The change SHALL NOT remove existing CLI flags (`--duration`, `--project`,
   `--series`, `--director-mode`, etc.); `--duration` SHALL be treated as a `cli_flag`
   provenance lock on `total_duration_min`, and `--project` SHALL actually load the
   matching `projects/{name}.yaml` overrides.
4. WHEN the LLM/Ollama or web search is unavailable THEN the system SHALL fall back to
   documented defaults for structural decisions and continue (matching current
   resilience).

### Requirement 10: Shared blackboard state model

**User Story:** As a developer, I want all agents to read from and write to one shared,
structured workspace instead of one agent handing values down to the next, so that
decisions cannot be silently dropped between stages.

#### Acceptance Criteria

1. The system SHALL treat the decision record plus project/story memory as a single
   shared workspace (blackboard) that the Director, Writer, story planner, and pipeline
   all read from and write to.
2. WHEN any stage needs a structural value THEN it SHALL read it from the shared
   workspace rather than recomputing it locally (this replaces the current
   master–slave hand-down where the pipeline re-derives values).
3. The shared workspace SHALL be persisted on disk/RAM (e.g. JSON), NOT in GPU memory,
   and SHALL NOT require any model to be loaded in order to read or write it.
4. WHERE multiple processes access the workspace concurrently (e.g. dashboard server
   plus a running pipeline) THEN access SHALL be serialized with file/thread locking to
   prevent corruption, reusing the existing locking approach.
5. Each write to the workspace SHALL preserve provenance so the origin of every value
   remains auditable.

### Requirement 11: Risk-tiered human intervention

**User Story:** As an operator, I want to be asked only about decisions that materially
affect the output, so that I am not forced to approve trivial choices.

#### Acceptance Criteria

1. The system SHALL classify decisions into risk tiers: high-impact structural
   decisions (duration, segment count, words/segment, images/segment) and low-impact
   cosmetic decisions (transition style, music style, etc.).
2. WHEN a high-impact decision is made THEN the system SHALL offer the user an explicit
   intervention/lock point before proceeding.
3. WHERE a decision is low-impact THEN the Director MAY decide it without prompting,
   recording provenance `director`.
4. The intervention points SHALL be driven by the existing impact ranking
   (e.g. the `impact_order` used in `consult_on_config`) rather than prompting on
   every field.
5. IF the user is non-interactive or times out THEN high-impact decisions SHALL fall
   back to the Director/Writer-agreed values and log the auto-proceed.

### Requirement 12: Three-tier memory separation

**User Story:** As an operator running multiple stories in a project, I want continuity
data organized so that shared project knowledge and per-story state do not contaminate
each other.

#### Acceptance Criteria

1. The system SHALL separate memory into three tiers: project store (shared across
   stories: characters, world lore, recurring motifs, visual locks, prior decision
   records), story store (per story: segments, this story's arc and decision record),
   and continuity/audit log (per story).
2. WHEN a new story is created in a project THEN it SHALL read shared continuity from
   the project store and write its own state only to its story store.
3. WHERE a one-time-use run is performed THEN it SHALL use an isolated story store and
   SHALL NOT write to any project store.
4. The memory layout SHALL be addressable per project and per story so multiple
   projects and stories can coexist without collision.
5. The split SHALL preserve existing continuity behavior (character/motif reuse and
   continuity auditing) at least as well as the current flat `PermanentMemoryLog`.

### Requirement 13: Per-character visual lock

**User Story:** As an operator producing long videos, I want each character's
appearance to stay stable across many image generations so the video looks consistent.

#### Acceptance Criteria

1. WHEN a character is established THEN the system SHALL store a visual lock for that
   character (canonical description and, where available, a seed and/or trained LoRA
   reference) in the project store.
2. WHEN generating images for any segment THEN the system SHALL apply the stored visual
   lock for each character present (continuing the existing description-injection and
   LoRA face-lock behavior).
3. WHERE a visual lock exists for a character THEN it SHALL be reused across all
   stories in the same project for continuity.
4. The visual lock SHALL be part of the shared project store (Requirement 12) and
   carry provenance.
5. IF no visual lock can be created (e.g. description too sparse) THEN the system SHALL
   log the skip and fall back to current behavior without failing the run.

### Requirement 14: Genuine Writer consent on structure

**User Story:** As the Director, I want the Writer to actually review my proposed
structural plan and respond to it, so that "Writer's consent" reflects real
collaboration rather than numbers produced in isolation.

#### Acceptance Criteria

1. WHEN the Writer is consulted THEN it SHALL be given the Director's proposed
   structural values (duration, segment count, words/segment, images/segment) plus the
   vision context, and SHALL respond with either agreement or specific adjustments and
   a rationale.
2. WHERE the Writer proposes an adjustment THEN the system SHALL record both the
   Director's original value and the Writer's adjusted value with provenance, so the
   change is auditable.
3. The script-writing stage (`build_segment_prompt` / per-segment generation) SHALL
   consume the agreed structural values from the shared workspace, NOT a separately
   computed number, so the value the Writer consented to is the value actually used.
4. IF the Writer returns no actionable response THEN the system SHALL proceed with the
   Director's proposal and log that consent defaulted.
5. The consent exchange SHALL be a one-time pre-production step (per Performance
   requirements), not a per-segment negotiation.

### Requirement 15: Word-count enforcement per segment

**User Story:** As an operator, I want generated segment scripts to actually match the
agreed word target so that the planned video length and pacing hold.

#### Acceptance Criteria

1. WHEN a segment script is generated THEN the system SHALL measure its actual word
   count against the agreed target for that segment.
2. WHERE the actual word count deviates beyond a documented tolerance band (e.g. ±X%)
   THEN the system SHALL attempt a bounded correction (regeneration and/or trim/pad)
   before accepting the script.
3. The correction SHALL have a bounded retry limit so it cannot loop indefinitely, and
   the system SHALL accept the best available result after the limit and log it.
4. WHERE a per-segment word count is user-locked (Requirement 3) THEN the tolerance
   band SHALL be tightest for that segment.
5. The enforcement SHALL extend the existing `validate_script` min/max-words check
   rather than introduce a parallel mechanism.
6. Word-count enforcement SHALL NOT add per-segment image-generation or extra heavy
   GPU calls; corrections SHALL be limited to the (light) LLM text stage.

### Requirement 16: Decision-record schema versioning and migration

**User Story:** As an operator who resumes long runs and updates the software, I want
persisted decision records to keep working across versions so a resumed run never
crashes on an old record.

#### Acceptance Criteria

1. The decision record SHALL include a schema `version` field.
2. WHEN a decision record is loaded THEN the system SHALL check its version against the
   current schema version.
3. WHERE a loaded record is from an older version THEN the system SHALL migrate it to
   the current schema (filling new fields with documented defaults and provenance
   `default`) rather than failing.
4. IF a record cannot be migrated THEN the system SHALL log a clear warning and rebuild
   a fresh decision record from current config, without crashing the run.
5. Newly written decision records SHALL always carry the current schema version.

### Requirement 17: Decision provenance report in the run manifest

**User Story:** As an operator, I want the final run manifest to show every structural
decision and where it came from, so I can answer "why is this video shaped this way?"
at a glance.

#### Acceptance Criteria

1. WHEN a run completes THEN the run manifest (`run_manifest.json`) SHALL include the
   full decision record, including each structural value and its provenance
   (`default` | `director` | `writer` | `user` | `cli_flag`).
2. The manifest SHALL record any clamps, conflict resolutions, and word-count
   corrections that occurred, so deviations from the plan are traceable.
3. WHERE the user locked or overrode a value THEN the manifest SHALL clearly mark it as
   user-imposed.
4. The manifest SHALL record the resolved segment count, per-segment word targets, and
   image counts actually used, alongside their sources.
5. Producing the manifest SHALL remain a lightweight, end-of-run operation and SHALL
   NOT add model calls.

## Performance and Non-Goals

The decision/coordination layer introduced by this spec MUST be effectively free at
runtime. The pipeline's cost is dominated by model inference and VRAM model-swapping on
the constrained (6GB) GPU; this spec MUST NOT add to that.

1. The decision record and shared workspace SHALL be disk/RAM-based; reads/writes SHALL
   be negligible relative to a single LLM or image-generation call.
2. The design SHALL NOT introduce any requirement to hold multiple models in GPU memory
   simultaneously; the existing one-heavy-task-at-a-time scheduler
   (`WorkloadScheduler`, HEAVY semaphore = 1) and model eviction (Ollama
   `keep_alive: 0`, `unload_sd_pipeline()`) SHALL remain the execution model.
3. The decision/coordination layer SHALL NOT add any per-segment model calls.
4. The Director↔Writer consent handshake SHALL be a one-time, pre-production step
   (a small, bounded number of LLM calls per run), not per segment.
5. Cliffhanger suggestion and story compaction SHALL be one-time, pre-production steps
   that run ONLY when the user explicitly chooses them.
6. Honoring agreed structural decisions SHOULD reduce wasted regeneration (e.g. from
   segment-count/word-count mismatches) and SHALL NOT increase it.

## Out of Scope

- Changing TTS engines, image-generation models, or rendering internals.
- Altering the creative content of prompts beyond what is needed to carry structural
  decisions.
- The unused/duplicate non-structural modules already removed in prior cleanup.
- Multi-user or remote hosting concerns (the API remains localhost-only by design).
- **Front-end presentation** of project selection / one-time-use / Dropbox-style
  source intake. This spec defines the **backend** capabilities only; the UI that
  exposes them is a later, separate effort.
