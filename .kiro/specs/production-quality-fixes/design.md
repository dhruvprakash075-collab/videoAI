# Design Document

## Overview

This design fixes the production-phase defects catalogued in `BUGS.md`, with emphasis on
reconnecting features that are built but dropped at the seams. It is organized into five
workstreams that can ship independently:

1. **Audio chain** — emotion + mood-pacing + Devanagari + OmniVoice perf (B8–B10, B16, B21).
2. **Visual identity** — fixed seed + CLIP prompt budget + num_images (B4, B5, B6, B11).
3. **Subtitle correctness** — Devanagari subs + word timestamps + font (B1, B2, B7, B40).
4. **Reliability** — retry policy + CrewAI lock + renderer portability (B13–B15, B19).
5. **Originality + cleanup** — purge IP, reconcile config, dead code (B17, B18, B20,
   B22–B38).
6. **Devanagari loanwords & environment imagery** — transliteration check + env-frame
   prompt priority (R2.6/2.7, R3.7).
7. **Model-switch readiness** — config-pure image gen, TTS engine registry, capability
   profiles, eval harness, acceleration adapter (DMD2/Hyper-SD/LCM), upscaler
   (4x-UltraSharp), Hindi voice candidates (IndicF5/OpenVoice v2) (R12).
8. **Operator preview & approval gate** — see/hear a sample before full production (R13).

Design priorities: preserve existing fallbacks (never crash a long run), add no heavy
GPU calls in text-stage fixes, and keep the one-heavy-task scheduler untouched.

### Research-grounded decisions

- **Voice-clone reference length 5–15s** is the consensus sweet spot across multiple TTS
  systems; longer is not better ([F5-TTS](https://localaimaster.com/blog/f5-tts-setup-guide),
  [Inworld](https://docs.inworld.ai/docs/tts/voice-cloning)). Content rephrased for compliance.
- **Character consistency** on a single GPU is best served (training-free) by a **fixed
  seed + identity-first prompt**, with LoRA as the heavier option
  ([Apatero](http://www.apatero.com/blog/character-consistency-multiple-images-ai-generation-fix-2025)).
  Content rephrased for compliance.
- **Devanagari TTS** quality improves when English loanwords are transliterated
  phonetically into Devanagari rather than left as Latin text
  ([smallest.ai](http://smallest.ai/blog/english-to-hindi-text-to-speech-how-to-choose-a-bilingual-tts-tool)).
  Content rephrased for compliance.

## Architecture

```
Per-segment flow (fixed):

  English script ──► Devanagari translate ──► emotion shape (Devanagari-aware)
                                                     │
                                          mood rate ─┤
                                                     ▼
                                            TTS (OmniVoice persistent worker)
                                                     │  + word timestamps
                                                     ▼
  prompt assembly (identity-first, token-budgeted, fixed seed) ──► SD images
                                                     │
                                                     ▼
  renderer ◄── Devanagari subtitle text + word timestamps ──► segment MP4
```

### Components touched

| Workstream | Files |
|---|---|
| Audio chain | `utils/emotion_control.py`, `core/pipeline_long.py`, `audio/audio_proxy.py`, `audio/omnivoice_worker.py` |
| Visual identity | `video/image_gen/image_gen.py`, `utils/scene_director.py`, `core/pipeline_long.py`, `memory/project_store.py` |
| Subtitles | `core/pipeline_long.py`, `video/renderer/renderer.py`, `video/renderer/assembler.py` |
| Reliability | `utils/retry_manager.py`, `utils/context_manager.py`, `video/renderer/renderer.py` |
| Originality + cleanup | `config/config.yaml`, `config/config.py`, `config/config_schema.py`, `config/config_schemas.py`, `utils/utils.py`, `utils/local_ui.py`, `agents/*.py`, `utils/specialized_models.py`, `utils/web_search.py`, `audio/audio_fx.py`, `tests/` |

## Components and Interfaces

### Workstream 1 — Audio chain

**Devanagari-aware emotion shaping** (`utils/emotion_control.py`)
- Add `_DEVANAGARI = True` detection (presence of `[\u0900-\u097F]`).
- New sentence-boundary handling for `।`/`?`/`!`; ellipsis/dash injection adapted so it
  operates on Devanagari punctuation, not just Latin `. `.
- `inject_emotion(script, mood, lang="hi")` — when `lang == "hi"` use the Devanagari
  rules; else the existing Latin rules.

**Pipeline wiring** (`core/pipeline_long.py`)
- Order fix: translate to Devanagari first, THEN `inject_emotion(devanagari_script, mood,
  lang="hi")`, and use that as `script_for_tts`. Removes the overwrite that discards
  emotion (B8).
- Pass `get_mood_rate(mood)` into `tts_generate(..., speed=...)` (B9).

**TTS speed plumbing** (`audio/audio_proxy.py`)
- `tts_generate(..., speed: float | None = None)`; when set, override the omnivoice
  config speed for that call. Pass `--speed` to the worker.

**OmniVoice persistent worker** (`audio/omnivoice_worker.py`, `audio/audio_proxy.py`)
- Add a long-lived worker mode: the worker loads the model once and reads
  text/output-path requests from stdin (one JSON request per line), emitting one JSON
  response per line. `audio_proxy` keeps the subprocess alive across segments and pipes
  requests.
- Fallback: if the persistent worker fails to start, use the existing per-call
  subprocess (B16). Chunk threshold tuned + seam crossfade note (B21).
- Reference-audio validator: a helper that checks `narration_voice.wav` is 5–15s mono,
  trims/warns otherwise (one-time per run).

### Workstream 2 — Visual identity

**Fixed seed** (`video/image_gen/image_gen.py`)
- `_stable_diffusion(...)` accepts an optional per-frame seed map; for each character
  present in a frame, use the visual-lock seed (or a deterministic
  `int(hashlib.sha256(char_key).hexdigest()[:8], 16)` fallback). Build
  `torch.Generator(device).manual_seed(seed)` and pass `generator=` to the pipeline call
  (B5, R3.6).

**Prompt token budget** (`utils/scene_director.py` + pipeline assembly)
- New helper `assemble_prompt(identity_tokens, scene_tokens, style_tokens, budget=70)`:
  places identity tokens first, then scene, then style; trims style/camera boilerplate
  before identity; counts approximate CLIP tokens (word-based heuristic) and caps to the
  budget (B4, R3.2/3.3).
- Remove the `photorealistic, masterpiece` positive tokens when the negative prompt
  forbids `photorealistic` (B6).

**num_images authority** (`core/pipeline_long.py`)
- Ensure the count from `plan["num_images"]` (already in `build_prompts`) is not diluted
  by enrich/merge; the SD call renders exactly that many prompts (B11).

### Workstream 3 — Subtitles

**Pass the right text + timestamps** (`core/pipeline_long.py`)
- `render_with_assets(..., subtitle_script=<devanagari or english>,
  word_timestamps_json=word_timestamps_json)`. Subtitle text = the same
  `script_for_tts` used for audio (B1, B2).

**Renderer signature** (`video/renderer/renderer.py`)
- `render_with_assets` and `build_html` accept `subtitle_script` and
  `word_timestamps_json`; HTML caption style uses a Devanagari-capable font
  (Noto Sans Devanagari with sans-serif fallback) (B7).
- When word timestamps exist, build captions from them; else proportional (B40).
- `assembler.create_segment_mp4` already accepts `word_timestamps_json` — thread it from
  the renderer fallback.

### Workstream 4 — Reliability

**Retry policy** (`utils/retry_manager.py`)
- Split exceptions: `TRANSIENT = (ConnectionError, TimeoutError, subprocess.TimeoutExpired)`
  retried up to `MAX_RETRIES` (50); `BOUNDED = (RuntimeError, OSError)` retried at most
  `BOUNDED_RETRIES` (3) (B13).
- Stop wrapping `generate_images` with the outer retry (it has internal OOM tiers), or
  wrap it only for transient errors (B14).
- Remove the dead `urlopen`-name branch (B29).

**CrewAI lock in compression** (`utils/context_manager.py`)
- `_llm_compress` acquires the shared `_crewai_lock` (import the module-level lock from
  `core.pipeline_long`, or accept a lock parameter). If lock not acquired within a
  timeout, fall back to the non-LLM summary (B15, R5.2).

**Renderer portability** (`video/renderer/renderer.py`)
- Read WSL distro/user from env (`VIDEOAI_WSL_DISTRO`, `VIDEOAI_WSL_USER`) and derive the
  project path from `Path.cwd()` mapped to `/mnt/<drive>`; default to current behavior if
  unset. Detect WSL availability before attempting (B19).

### Workstream 5 — Originality + cleanup

**Purge IP** — replace named characters in `config.yaml` and `config.py._default_config`
with original placeholders; change code fallbacks (`utils/utils.py`,
`utils/local_ui.py`, `agents/executive_agent.py`, `agents/director_agent.py`) to
`"narrator"`; neutralize the image-engineer few-shot example
(`utils/specialized_models.py`); update docstring in `memory/memory.py`; update test
fixtures (B23–B27).

**Original-content gating** — when `run_mode`/original flag is set, skip franchise web
research (B26). (Reuses the run-mode from the decision record.)

**Reviewer parsing** — `review_script_fast` uses a brace-depth JSON extractor (port the
Director's `_parse_json` approach) (B17).

**Config reconciliation** — align `words_per_segment` defaults (B37), OmniVoice
fallbacks (B36), Wikipedia section matching (B18), cache-key defaults (B20). Remove dead
constants (B28, B33, B34, B38). SFX map trimmed to existing files (B32).

### Workstream 6 — Devanagari loanwords & environment imagery (cross-cutting)

**Loanword transliteration** (`agents/director_agent.translate_to_devanagari`,
post-check)
- Strengthen the translation prompt to require phonetic Devanagari for English
  loanwords and Hindi-word numbers (already partly present — make it explicit and
  testable).
- Add a `_devanagari_ratio(text)` helper: fraction of letters in `[\u0900-\u097F]`. After
  translation, if Latin-letter content exceeds a small threshold, log a warning (and
  optionally request a re-translation pass, bounded). Reuses the existing translator
  model; one bounded call max (R2.6, R2.7).

**Environment imagery** (`utils/scene_director.enrich_prompts` + prompt assembler)
- For low-character-presence frames, the token-budgeted assembler (Workstream 2)
  prioritizes setting/atmosphere/lighting tokens and omits character identity tokens, so
  establishing shots keep world detail (R3.7).

### Workstream 7 — Model-switch readiness (image + TTS)

**TTS engine adapters** (`audio/audio_proxy.py`)
- Refactor `tts_generate` to dispatch via an engine registry:
  `_TTS_ENGINES = {"omnivoice": _call_omnivoice_worker, "edge": _call_edge_direct, ...}`.
  Adding an engine = adding one adapter; the per-segment flow is unchanged (R12.3).
- Each adapter declares a small capability dict (cloning support, languages, VRAM hint)
  exposed via a `tts_capabilities()` function (R12.5).

**Image model config-purity** (`video/image_gen/image_gen.py`)
- Audit `_stable_diffusion` for any model-specific assumptions; ensure steps, guidance,
  scheduler, width, height, negative prompt all come from config (R12.1, R12.2). The
  scheduler is already DPM++; make it config-selectable (`image_gen.scheduler`).
- Cache key already includes steps/size/guidance; add the active model id
  (`sd_model_path`) to the key so switching models invalidates stale cache (R12.4, B20).

**Capability profiles + eval harness** (new small module, e.g. `utils/model_eval.py`)
- A documented profile table for the current image model and TTS engines (R12.5).
- `evaluate_models(sample_prompts, sample_text)` — generates a handful of fixed-prompt
  images from the current SD model and one short TTS clip from the current voice, writing
  them to a `model_eval/` folder for A/B comparison. Opt-in CLI flag `--eval-models`;
  does NOT run a full video (R12.6).
- Unavailable model/engine → clear error + documented default fallback (R12.7).

**Acceleration adapter** (`video/image_gen/image_gen.py`, config)
- Config slot `image_gen.acceleration` = `{type: dmd2|hyper_sd|lcm|none, lora_path, steps,
  guidance_scale, sampler}`. When set, load the distillation LoRA and apply its
  step/guidance/sampler overrides (R12.8). Default `none` until benchmarked.
- DMD2 is the recommended first candidate (preserves normal guidance → better prompt
  adherence at low steps); LCM/Hyper-SD supported via the same slot.

**Upscaler** (config + a small `utils/upscale.py` or reuse Real-ESRGAN)
- Config `image_gen.upscaler` = `{model: 4x-UltraSharp|realesrgan|none, scale, tile}`.
  Generate at a smaller base (e.g. 512–640) then upscale to 1080p (R8, R12.9). Tiled to
  fit 6GB VRAM. Default candidate: 4x-UltraSharp.

**Candidate registry for evaluation** (documentation + config examples)
- Image: current SD 1.5 checkpoint + at least one alternative semi-realistic/anime SD
  1.5 checkpoint (and optionally an Arcane-style LoRA).
- TTS: OmniVoice (current) + IndicF5 (Indian-language F5-TTS) and/or OpenVoice v2
  (emotion control) as Hindi-focused candidates (R12.10). All behind the engine registry
  + capability profiles; none committed as default without operator approval.

### Workstream 8 — Operator preview & approval gate

**Preview gate** (`core/pipeline_long.py`, reuse `UIState`/Director Mode)
- New opt-in flag `--preview` (and API field). When set, after generating the first
  segment's sample images + a short narration clip, the pipeline pauses via the existing
  human-in-the-loop mechanism and surfaces the sample paths (R13.1, R13.2, R13.6).
- Operator approves → continue with the same settings; rejects → stop, leaving samples
  for inspection (R13.3).
- Configurable timeout with documented attended-default (proceed) vs safe-default
  (abort) behavior (R13.5).
- Off by default so batch/unattended runs are unaffected (R13.4).
- The eval harness (`--eval-models`) and the preview gate (`--preview`) share the sample
  writing/review surface so the operator always sees/hears before committing.

## Data Models

No new persistent models. Reuses:
- `DecisionRecord` (run_mode, durations) from the prior spec.
- `ProjectStore.visual_locks[char] = {description, seed, lora_path, provenance}` —
  the `seed` field becomes actively used.

## Correctness Properties

### Property 1: Subtitle/audio language parity

For any rendered segment, the subtitle source text equals the text sent to TTS (same
language and content).

**Validates: Requirements 1.1, 1.5**

### Property 2: Emotion reaches the spoken script

When `tts.lang == "hi"`, the text sent to TTS has had Devanagari-aware emotion shaping
applied (it is not the raw translation), and the TTS speed equals the mood rate for the
segment's mood.

**Validates: Requirements 2.1, 2.2**

### Property 3: Identity tokens survive truncation

For any generated image prompt, the character identity tokens appear within the first
~77 CLIP tokens and are never the tokens dropped by truncation.

**Validates: Requirements 3.2, 3.3**

### Property 4: Deterministic seed reproducibility

Generating the same character twice with the same visual-lock seed yields the same
generator seed; absent a stored seed, the fallback seed is a stable function of the
character key.

**Validates: Requirements 3.1, 3.6**

### Property 5: Bounded deterministic retries

A deterministic error (non-network) is retried at most `BOUNDED_RETRIES` times; only
transient errors use the endurance limit.

**Validates: Requirements 4.2, 4.3**

### Property 6: Serialized CrewAI execution

Every CrewAI `kickoff()` in the codebase, including context compression, executes while
holding the shared serialization lock.

**Validates: Requirements 5.1**

### Property 7: No IP terms in defaults

The default config, code fallbacks, few-shot examples, and test fixtures contain no
franchise character names or terms.

**Validates: Requirements 7.1, 7.2, 7.3, 7.5**

### Property 8: No added heavy GPU calls

The text-stage fixes (emotion, subtitle, prompt budget, reviewer parsing) invoke no
image-generation or extra heavy GPU work.

**Validates: Requirements 2.5**

### Property 9: Devanagari output purity

For a Hindi run, the text sent to TTS is predominantly Devanagari (Devanagari-letter
ratio above the configured threshold); English loanwords are transliterated rather than
left in Latin, and a warning is logged when the threshold is not met.

**Validates: Requirements 2.6, 2.7**

### Property 10: Engine/model selection is config-driven

Switching `tts.engine` routes to the matching adapter with no per-segment flow change,
and switching `image_gen.sd_model`/`sd_model_path` changes generation and invalidates the
cache (the active model id is part of the cache key) without code changes.

**Validates: Requirements 12.1, 12.3, 12.4**

### Property 11: Preview gate blocks full production until approval

When `--preview` is set, the pipeline does not produce segments beyond the sample until
the operator approves; on rejection it stops; when off, runs proceed unattended.

**Validates: Requirements 13.2, 13.3, 13.4**

### Property 12: Optional speed/quality changes are off until measured

The acceleration adapter and alternative engines default to off/current; enabling them
is config-driven, and the benchmark records seconds-per-image/segment before and after.

**Validates: Requirements 12.8, 12.10**

**Validates: Requirements 2.5, Performance 1**

## Error Handling

| Condition | Behavior |
|---|---|
| Devanagari emotion shaping fails | Use plain translated script; log; continue (R2.4) |
| Mood rate invalid | Use config default speed |
| Visual-lock seed missing | Deterministic fallback seed from char key (R3.6) |
| Prompt still over budget after trim | Hard-truncate non-identity tokens, keep identity |
| Persistent OmniVoice worker fails to start | Per-call subprocess fallback (R6.4) |
| Reference audio outside 5–15s | Trim/pad + warn; never fail the run |
| Word timestamps missing | Proportional subtitle timing (R1.3) |
| CrewAI lock timeout in compression | Non-LLM summary fallback (R5.2) |
| WSL/Hyperframes unavailable | Assembler fallback + clear log (R9.2) |
| Reviewer JSON unparsable | Auto-approve + warning (R10.2) |

## Testing Strategy

Unit (no GPU/LLM; mocks assert no heavy calls):
- Devanagari emotion shaping: `।`-boundary handling; Latin path unchanged; lang switch.
- Mood rate → speed plumbing (mock `tts_generate`, assert speed passed).
- Prompt budget assembler: identity-first ordering; truncation keeps identity; token cap.
- Deterministic seed: same key → same seed; stored seed used when present.
- Retry policy: transient vs deterministic counts; `generate_images` not double-wrapped.
- Reviewer brace-depth parser: nested JSON parsed; garbage → auto-approve.
- Originality: grep-style assertion that defaults/fallbacks contain no franchise terms.
- Renderer portability: env-driven distro/user/path resolution (no actual WSL call).

Integration (light/dry-run):
- Subtitle text equals TTS text for a hi run (dry-run captures the strings).
- Config reconciliation: `words_per_segment` default identical across all three sources.

On-hardware (manual, documented — not CI):
- A real `--file` Hindi run: confirm subs match audio, faces consistent across segments,
  OmniVoice loads once, 1080p output is crisp.

## Migration & Rollout

Order (each independently shippable, low blast radius first):
1. Originality purge + config reconciliation (pure replacements, no behavior risk).
2. Subtitle text/timestamp fix (correctness, isolated to renderer call).
3. Emotion/mood-rate reconnection (text-stage).
4. Visual identity (seed + prompt budget).
5. Reliability (retry policy, CrewAI lock, renderer portability).
6. OmniVoice persistent worker (largest change; behind a fallback).

## Open Questions

1. **Resolution strategy (B3):** generate at 960×540 + upscale, or render at native
   768×432? Needs a VRAM measurement on the 4050. Default proposal: 960×540 + Lanczos/
   Real-ESRGAN upscale to 1080p if it fits; else native.
2. **OmniVoice persistent worker:** stdin/stdout protocol vs a small local socket?
   Proposal: line-delimited JSON over stdin/stdout (simplest, no port).
3. **Devanagari caption font:** bundle Noto Sans Devanagari, or rely on a system font?
   Proposal: bundle to guarantee glyph coverage.
