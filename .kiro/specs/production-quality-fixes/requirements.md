# Requirements Document

## Introduction

A deep audit of the production phase (TTS, visual generation, rendering, agents) found
40 issues, catalogued in `BUGS.md`. The dominant pattern: several high-value features
(emotion shaping, mood pacing, Devanagari narration, character-face consistency) are
**implemented but disconnected at the seams** — the wiring drops them right before they
reach the output, so finished videos look and sound worse than the code's feature set
implies.

This spec turns the actionable subset of that catalog into fixable requirements,
prioritizing the operator's stated preferences:

- Narration in Devanagari Hindi with English loanwords transliterated phonetically.
- Voice cloning is critical; prefer OmniVoice over edge-tts.
- Character faces must stay visually consistent across images/segments.
- Stills + narration; world/environment imagery matters.
- All-original content — no IP names or descriptions from existing franchises.
- Tuned for YouTube output on a 6GB (RTX 4050) GPU.

Scope is the bug fixes and feature reconnections. It builds on the
`director-decision-authority` spec already implemented (DecisionRecord, project memory,
visual locks).

## Glossary

- **Production phase**: everything after pre-production — per-segment TTS, image
  generation, rendering, and final assembly.
- **Devanagari script**: the Hindi writing system; sentence boundary is `।`.
- **Visual lock**: a stored per-character appearance reference (description, seed, LoRA)
  in the project store; introduced in the director-decision-authority spec.
- **CLIP token limit**: Stable Diffusion's text encoder silently truncates prompts at
  ~77 tokens.
- **Emotion shaping**: punctuation/pacing markers injected to guide TTS prosody
  (`utils/emotion_control`).
- **Mood rate**: per-mood TTS speed multiplier from `get_mood_rate`.
- **Transient vs deterministic failure**: transient = network/timeout (retry helps);
  deterministic = bad input/missing model/persistent OOM (retry just hangs).
- **BUG IDs**: references like (B1) point to entries in `BUGS.md`.

## Requirements

### Requirement 1: Subtitles match the spoken narration

**User Story:** As an operator, I want the burned-in subtitles to match the Devanagari
voice-over, so viewers read what they hear.

#### Acceptance Criteria

1. WHEN a segment is rendered THEN the subtitle text SHALL be derived from the same
   script that was sent to TTS (Devanagari when `tts.lang == "hi"`), not the English
   script (B1).
2. WHERE word-level timestamps were produced during TTS THEN they SHALL be passed
   through to the renderer and used for word-synced subtitles (B2).
3. IF no word timestamps are available THEN the renderer SHALL fall back to
   proportional timing without crashing.
4. WHEN subtitles are rendered via the Hyperframes HTML path THEN the caption font
   SHALL render Devanagari glyphs correctly (B7).
5. The subtitle language SHALL be consistent between the assembler path and the
   Hyperframes path.

### Requirement 2: Emotion and mood pacing reach the TTS engine

**User Story:** As an operator, I want emotional delivery and mood-based pacing to
actually affect the Hindi narration, so the voice-over feels alive.

#### Acceptance Criteria

1. WHEN the narration language is Hindi THEN emotion shaping SHALL be applied to the
   Devanagari text actually sent to TTS (not discarded), using Devanagari-aware
   sentence boundaries (B8, B10).
2. WHEN a segment has a mood THEN the mood rate from `get_mood_rate` SHALL be passed to
   the TTS engine so speed varies by mood (B9).
3. WHERE the TTS engine is OmniVoice THEN the per-segment speed SHALL be settable per
   call, overriding the static config default for that segment.
4. IF emotion shaping or mood rate cannot be applied THEN the system SHALL fall back to
   the plain script and default speed, logging the fallback (no crash).
5. The emotion/pacing application SHALL remain a per-segment text-stage operation and
   SHALL NOT add heavy GPU calls.
6. WHEN narration is generated for Hindi THEN English loanwords SHALL be transliterated
   phonetically into Devanagari (e.g. फोन, स्कूल, कैमरा) rather than left in the Latin
   alphabet, and numbers SHALL be spelled out in Hindi words.
7. The text sent to a Hindi TTS engine SHALL contain no stray Latin-alphabet words
   (other than unavoidable proper-noun edge cases), verifiable by a Devanagari-ratio
   check that logs a warning when Latin content exceeds a small threshold.

### Requirement 3: Character face consistency is preserved

**User Story:** As an operator, I want each character's face to stay consistent across
all images, so the video looks coherent.

#### Acceptance Criteria

1. WHEN generating images for a character THEN the stored per-character seed from the
   visual lock SHALL be applied to the diffusion generator (B5).
2. WHEN assembling an image prompt THEN the character identity tokens SHALL be placed
   so they survive the CLIP token limit (e.g. early in the prompt) and SHALL NOT be the
   tokens that get truncated (B4).
3. The prompt assembly SHALL keep the total within a documented token budget, trimming
   boilerplate (camera/style filler) before identity tokens.
4. WHERE a positive style token contradicts the negative prompt (e.g.
   `photorealistic` in both) THEN the conflict SHALL be removed (B6).
5. The number of images per segment SHALL honor the Director/Writer-agreed `num_images`
   through to generation (B11).
6. IF a visual lock has no seed THEN generation SHALL proceed with a deterministic
   per-character fallback seed derived from the character key (stable across runs).
7. WHERE a frame is environmental (low character presence) THEN the prompt budget SHALL
   prioritize world/environment detail tokens (setting, atmosphere, lighting) so
   establishing shots retain richness, and character-identity tokens SHALL be omitted or
   minimized for those frames.

### Requirement 4: Reliable retry behavior (no multi-minute hangs)

**User Story:** As an operator, I want failures to fail fast when retrying won't help,
so a run never hangs for many minutes per segment.

#### Acceptance Criteria

1. WHEN a transient error occurs (connection/timeout) THEN the system MAY retry with
   backoff up to the endurance limit.
2. WHEN a deterministic error occurs (bad input, missing model, persistent OOM) THEN
   the system SHALL retry at most a small bounded number of times (e.g. ≤3) before
   failing (B13).
3. WHERE a function has its own internal recovery (e.g. `generate_images` 3-tier OOM)
   THEN the outer retry SHALL NOT re-wrap it in a way that compounds retries (B14).
4. The retry policy SHALL distinguish exception classes rather than treating all
   `RuntimeError`s as transient.

### Requirement 5: Concurrency safety in context compression

**User Story:** As a developer, I want all CrewAI executions serialized, so concurrent
kickoffs cannot corrupt the executor.

#### Acceptance Criteria

1. WHEN context-window compression triggers an LLM call THEN it SHALL acquire the same
   serialization lock used by all other CrewAI `kickoff()` calls (B15).
2. IF the lock cannot be acquired in a reasonable time THEN compression SHALL fall back
   to the non-LLM summary path rather than block indefinitely.
3. The compression LLM call SHALL remain bounded (not per-segment unbounded growth).

### Requirement 6: OmniVoice performance and voice-clone fidelity

**User Story:** As an operator producing long videos, I want OmniVoice to not reload the
model every segment and to keep the cloned voice consistent.

#### Acceptance Criteria

1. WHEN multiple segments are synthesized in a run THEN the OmniVoice model SHALL NOT be
   reloaded from scratch for every segment (B16).
2. The voice-clone reference audio SHALL be validated to a quality sweet spot (5–15s,
   mono) before use, trimming/warning if outside range.
3. WHERE a segment's audio exceeds the chunk threshold THEN chunk seams SHALL be handled
   so cloned-voice timbre stays consistent (B21).
4. IF the persistent/optimized worker is unavailable THEN the system SHALL fall back to
   the current per-call worker without failing.
5. OmniVoice SHALL remain the preferred engine, with edge-tts as fallback.

### Requirement 7: All-original content (no IP leakage)

**User Story:** As an operator, I want all shipped content to be original, so the videos
contain no characters, names, or descriptions from existing franchises.

#### Acceptance Criteria

1. The default config and code fallbacks SHALL NOT contain named characters or terms
   from existing franchises (B23, B24).
2. WHERE a character is unspecified THEN code fallbacks SHALL use generic original
   placeholders (e.g. `narrator`, `protagonist`).
3. The image-engineer few-shot example SHALL use a neutral, original description (B25).
4. WHERE original-content mode is active THEN web research SHALL be skipped or
   restricted to generic craft guidance, not franchise canon (B26).
5. Test fixtures and docstrings SHALL use original placeholder names (B27).

### Requirement 8: YouTube-quality visual output

**User Story:** As an operator, I want crisp 1080p output tuned for YouTube on my 6GB
GPU.

#### Acceptance Criteria

1. The produced video SHALL avoid heavy upscaling artifacts from a large mismatch
   between generated image resolution and output resolution (B3).
2. WHERE native 1080p generation does not fit the VRAM budget THEN the system SHALL
   generate at a higher intermediate resolution and apply a quality upscale, or render
   at native image resolution — chosen to fit 6GB.
3. The cache key SHALL use the same generation parameters as actual generation so cache
   hits/misses are correct (B20).
4. The final quality check SHALL compare duration/resolution against the resolved
   decision-record values, not raw config, to avoid false failures (B22).
5. Encoder settings SHALL remain tuned for YouTube (existing NVENC config preserved).

### Requirement 9: Renderer portability

**User Story:** As an operator, I want the renderer to work without hardcoded
machine-specific assumptions.

#### Acceptance Criteria

1. The Hyperframes renderer SHALL derive the WSL distro, user, and project path from
   environment/config rather than hardcoded values (B19).
2. IF the Hyperframes environment is unavailable THEN the system SHALL fall back to the
   assembler path and log the reason (existing behavior preserved).
3. The renderer SHALL NOT silently fall back on every run due to environment mismatch.

### Requirement 10: Correctness of the script-review path

**User Story:** As an operator, I want the fast script reviewer to actually parse model
output, so reviews are meaningful.

#### Acceptance Criteria

1. WHEN the reviewer returns nested JSON THEN the parser SHALL extract it correctly
   (brace-depth parsing), not fail to a silent auto-approve (B17).
2. IF parsing genuinely fails THEN auto-approve SHALL remain the safe fallback, with a
   warning logged.

### Requirement 11: Configuration consistency and cleanup

**User Story:** As a developer, I want config defaults and dead code reconciled, so the
codebase is consistent.

#### Acceptance Criteria

1. The `words_per_segment` default SHALL be consistent across `config_schema.py`,
   `config_schemas.py`, and `config.yaml` (B37).
2. OmniVoice fallback defaults in code SHALL match `config.yaml` (B36).
3. Wikipedia include/skip section lists SHALL not conflict (exact matching) (B18).
4. Dead constants and unused branches SHALL be removed or documented (B28, B29, B33,
   B34, B38).
5. The SFX keyword map SHALL only reference files that exist, or missing files SHALL be
   clearly handled (B32).

### Requirement 12: Model-switch readiness (image + TTS)

**User Story:** As an operator, I want to be able to swap the Stable Diffusion model and
the TTS engine, and fine-tune their settings, without rewriting pipeline code — so I can
chase the best look and voice for YouTube.

#### Acceptance Criteria

1. The image model SHALL be selectable purely via config (`image_gen.sd_model` /
   `sd_model_path`) with no model-specific assumptions hardcoded in the generation code
   beyond what config provides.
2. The image generation parameters (steps, guidance_scale, sampler/scheduler, width,
   height, negative prompt) SHALL all be read from config so a new model can be tuned
   without code changes.
3. The TTS engine SHALL be selectable via config (`tts.engine`), and the pipeline SHALL
   route to the correct engine adapter; adding a new engine SHALL require only a new
   adapter function, not changes to the per-segment flow.
4. WHERE a model/engine is switched THEN the cache key SHALL incorporate the active
   model identity so cached artifacts from a previous model are not reused incorrectly.
5. The system SHALL expose a documented capability profile per engine/model
   (e.g. supports voice cloning, supported languages, VRAM footprint, recommended
   settings) so the operator can compare options.
6. A lightweight, opt-in evaluation harness SHALL be available to generate a small
   fixed-prompt sample set from the current image model and a short TTS sample from the
   current voice, enabling A/B comparison between models — without running a full video.
7. IF a configured model/engine is unavailable THEN the system SHALL log a clear error
   and fall back to a documented default rather than crash mid-run.
8. The system SHALL support an optional **acceleration adapter** slot for the image
   model (step-distillation such as DMD2, Hyper-SD, or LCM) selected via config, with
   the matching step-count / guidance / sampler overrides; DMD2 is the recommended
   first candidate because it preserves normal guidance (prompt adherence) at low step
   counts. The acceleration adapter SHALL be off by default until validated.
9. The image upscale step (Requirement 8) SHALL use a config-selectable upscaler;
   **4x-UltraSharp** (or Real-ESRGAN) is the recommended first candidate.
10. The candidate engines/models documented for evaluation SHALL include, at minimum:
    image — the current SD 1.5 checkpoint plus at least one alternative
    semi-realistic/anime SD 1.5 checkpoint; TTS — OmniVoice (current) plus IndicF5
    (Indian-language F5-TTS) and/or OpenVoice v2 (emotion control) as Hindi-focused
    candidates. These are CANDIDATES to evaluate, not committed defaults.

### Requirement 13: Operator preview and approval gate

**User Story:** As an operator, I want to see sample images and hear a voice sample
before the full video is produced, so I can approve the look and sound myself.

#### Acceptance Criteria

1. The evaluation harness (Requirement 12.6) SHALL write its sample images and voice
   clip to a predictable folder and SHALL print/log their paths so the operator can
   open and review them.
2. WHERE preview mode is requested THEN the pipeline SHALL pause after generating a
   small sample (e.g. the first segment's images + a short narration clip) and SHALL
   wait for operator approval before producing the remaining segments.
3. WHEN the operator approves THEN production SHALL continue using the approved
   model/engine/settings; WHEN the operator rejects THEN the run SHALL stop without
   producing the full video, leaving samples for inspection.
4. The preview/approval gate SHALL be opt-in (CLI flag / API field) and SHALL default
   to off so unattended/batch runs are unaffected.
5. IF the operator does not respond within a configurable timeout THEN the system SHALL
   either proceed (attended-default) or abort (safe-default) per a documented setting.
6. The preview gate SHALL reuse the existing human-in-the-loop pause/resume mechanism
   (UIState / Director Mode) rather than introduce a parallel mechanism.

## Bug Coverage Map

Traceability of `BUGS.md` items to this spec. Every bug is either actioned by a
requirement or explicitly deferred.

Actioned:
- B1, B2, B7, B40 → Requirement 1 (subtitles)
- B8, B9, B10 → Requirement 2; B16, B21 → Requirement 6 (audio chain)
- B4, B5, B6, B11 → Requirement 3 (visual identity)
- B13, B14 → Requirement 4; B15 → Requirement 5; B17 → Requirement 10;
  B19 → Requirement 9 (reliability)
- B23, B24, B25, B26, B27 → Requirement 7 (originality)
- B3, B20, B22 → Requirement 8 (YouTube quality)
- B18, B28, B29, B32, B33, B34, B36, B37, B38 → Requirement 11 (config/cleanup)
- B12 (RVC/SFX/music defaults) → folded into Requirement 11 (documented defaults)

Deferred (low-impact tail; tracked in `BUGS.md`, not actioned in this spec):
- B30 (compat double-import; guarded, harmless), B31 (langchain warning filter;
  harmless), B35 (`num_ctx` hardcoded; works for current models), B39
  (name-strip regex fragility; mitigated once original single-token placeholders are
  used in Requirement 7). These may be revisited if they cause real issues.

## Out of Scope

- Adding new TTS or image-generation engines as part of THIS spec's fixes (Requirement
  12 makes the system *ready* to switch, but selecting/integrating a specific new model
  is a follow-up effort).
- The front-end dashboard UI changes.
- Per-segment word/image lock CLI exposure (deferred in the prior spec).
- A full ComfyUI/IP-Adapter pipeline rebuild — face consistency here is via seed +
  prompt budget + existing LoRA, not a new node graph.

## Performance and Non-Goals

1. All text-stage fixes (emotion, subtitles, prompt budget) SHALL add no heavy GPU
   calls.
2. The OmniVoice persistent-worker optimization SHALL reduce, not increase, total model
   load time.
3. No fix SHALL require holding multiple models in VRAM simultaneously; the existing
   one-heavy-task scheduler stays.
4. The acceleration adapter (R12.8) SHALL be measured to confirm it reduces
   seconds-per-image versus the current step count; it SHALL NOT be enabled by default
   until a benchmark shows a net speedup without unacceptable quality loss.
5. The upscale step (R8/R12.9) MAY add a small per-image cost, but combined with
   generating at a smaller base resolution it SHALL NOT increase total per-segment time
   relative to the current generate-large-then-stretch approach; this SHALL be verified
   by the benchmark.
6. Alternative TTS engines (R12.10) have UNKNOWN per-segment cost until measured; they
   SHALL only become a default after the benchmark + operator preview (R13) confirm
   acceptable quality and time.
7. A production-time benchmark SHALL record seconds-per-image and seconds-per-segment
   before and after each optional change, so speed claims are verified rather than
   assumed.
