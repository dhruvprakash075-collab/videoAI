# Output-Quality Fix Plan

Fixes the concrete problems observed in the first complete `the_last_lamplighter`
run (audio + 5 images produced; final video assembly timed out). Each item has
**Problem → Root cause → Fix → File(s) → Verify**. Implement top-to-bottom; they are
ordered by impact. Keep all changes config-driven and preserve existing fallbacks.

Context for the implementer:
- 6GB RTX 4050, Windows, serial pipeline (`performance.max_workers: 1`).
- Director = `Replete-Qwen` (7B), Writer = mode-aware (`cra-guided-7b` for scratch,
  `qwen3.5-9b-opus` for adaptation), TTS = OmniVoice (voice clone, now working),
  image = Stable Diffusion AnyLoRA.
- Run tests after each task: `venv\Scripts\python.exe -m pytest tests/ -q`.

---

## P0 — Video assembly times out (no final video)

**Problem:** `ffmpeg` Ken Burns assembly hit the 900s timeout and the run failed at the
last step ("No segments generated"). The audio + images were fine.

**Root cause:** `video/renderer/assembler.create_segment_mp4` builds ONE `zoompan`
(Ken Burns pan/zoom) filter per image and concatenates them for the WHOLE segment.
`zoompan` is extremely CPU-slow (it recomputes pan/zoom per output frame). For a
5.4-min segment (~7,750 frames @24fps) on this CPU it exceeds 15 min. Two compounding
issues: (a) the video was far too long (CRA wrote 353 words), (b) zoompan is too heavy.

**Fix (do all three):**
1. **Scale the timeout to video length** instead of a flat 900s:
   `timeout = max(900, int(duration * 12) + 300)` so long videos don't get killed.
2. **Make Ken Burns optional / lighter** via config `video.ken_burns` (default
   `"light"`):
   - `"off"`  → no zoompan; use a simple `scale`+`setsar` still image per clip
     (near-instant). Subtitles + fade still apply.
   - `"light"`→ keep zoompan but render it at a LOW internal fps and `scale` the
     base image only 1.25× (not 2×): change `scale={w}*2:{h}*2` → `scale=iw*1.25:-1`
     and add `zoompan ... :fps=12` then a final `fps={fps}` on the concat output.
     Halves frame count through the expensive filter.
   - `"full"` → current behavior (2× scale, full fps zoompan).
3. **Cap practical segment length**: the bigger driver is video length — see P1.

**Files:** `video/renderer/assembler.py` (timeout calc + ken_burns branch),
`config/config.yaml` (`video.ken_burns: "light"`), `config/config_schema.py`
(allow the key).

**Verify:** a ~1-min segment assembles in < 60s; a 5-min segment finishes (doesn't
time out). Output MP4 plays with subtitles burned.

---

## P1 — Video far too long / not ~1 minute

**Problem:** Asked for ~1 min; got ~5.4 min audio.

**Root cause:** CRA ignores the word target (wrote 353 words), and our widened
tolerance (0.6) + max_words 600 *accepts* that long script. Audio length drives video
length.

**Fix:** For **adaptation/`--file`** runs the writer is now `qwen3.5-9b-opus` (faithful,
obeys length). ALSO tighten the band for adaptation runs so length is respected:
- Add `script.words_per_segment_hard_cap` (e.g. 1.3×) and, when the writer is the
  faithful model, set `word_count_tolerance: 0.25` and `max_words: 250` for a 140-word
  target. Make these apply only in adaptation mode (don't re-trigger CRA's slow loops in
  scratch mode — keep its wide band).
- Practically: branch the two tolerances by mode (mirror the writer_scratch/writer_adapt
  split already added in `run_pre_production`). Store chosen tolerance in the config
  overlay so `process_segment` uses it.

**Files:** `core/pipeline_long.py` (set per-mode word tolerance alongside writer choice),
`config/config.yaml` (`script.adapt_tolerance: 0.25`, `script.scratch_tolerance: 0.6`).

**Verify:** a `--file` run with `--words-per-segment 140` yields a script of ~110–180
words and a video ~1 min.

---

## P2 — Hindi is archaic/literary, not how people speak today

**Problem:** The Devanagari narration sounds like very old Hindi nobody uses now.

**Root cause:** `translate_to_devanagari` prompt rule 7 says "Use conversational spoken
Hindi" but rule set still yields literary/Sanskritized output from the translator model,
and there's no explicit "modern, everyday, simple" instruction or banned-archaic-words
guidance.

**Fix:** Strengthen the translation prompt in
`agents/director_agent.translate_to_devanagari`:
- Add explicit rules:
  - "Use MODERN, everyday spoken Hindi (the Hindi used in daily conversation and modern
    YouTube narration), NOT literary, Sanskritized, or archaic Hindi."
  - "Avoid heavy tatsam/Sanskrit words; prefer common words people actually say
    (e.g., use 'रोशनी' not 'प्रकाश-पुंज', 'कोशिश' not 'प्रयास' where natural,
    'ज़िंदगी' not 'जीवन' when conversational)."
  - "Keep widely-understood English loanwords transliterated (फोन, लाइट, सिटी) as
    people naturally mix them in speech."
  - "Imagine a friendly modern narrator speaking to a young audience."
- Add a config knob `tts.hindi_register: "modern"` (default) vs `"literary"` so the
  prompt can be switched. Read it in the method.

**Files:** `agents/director_agent.py` (prompt), `config/config.yaml`
(`tts.hindi_register: "modern"`).

**Verify:** translated sample uses everyday vocabulary; spot-check a few sentences are
conversational, not archaic.

---

## P3 — Images are character-only; want world/environment (TPP, "establishing world")

**Problem:** Most frames are tight character shots; you want surroundings/world shots —
"third-person, see the world," more environment.

**Root cause:** Two things:
1. The Director's `char_presence` map assigned medium/high character weights to almost
   every frame (few < 0.3 environmental frames), so `enrich_prompts` rarely picks the
   "wide establishing shot of the environment" branch.
2. `build_prompts` core shots are character-centric.

**Fix:**
1. **Bias the plan toward environment frames.** In `utils/story_planner.py` plan prompt,
   strengthen the `char_presence` guidance: "At least 40% of frames MUST be environment/
   world shots (weight ≤ 0.2) showing the setting, landscape, architecture, and world —
   not the character. The opening and closing frames MUST be wide world establishing
   shots." (You already instruct variety; make environment a hard minimum.)
2. **Enforce it in code** (don't trust the LLM alone): in `core/pipeline_long.py` after
   the plan is parsed, post-process `char_presence` so a configurable fraction
   (`visual.environment_frame_ratio`, default 0.4) of frames are forced to ≤0.2 weight
   (set the lowest-weight frames to environment). 
3. **Make environmental prompts richer** in `utils/scene_director.enrich_prompts` low-
   weight branch: instead of generic "grand scenery / empty landscape", inject world
   tokens from the Director's vision (setting, locations, time of day, architecture).
   Pull a `world_description` from the vision doc / config and use it for env frames.

**Files:** `utils/story_planner.py` (plan prompt), `core/pipeline_long.py`
(force env ratio), `utils/scene_director.py` (richer env tokens),
`config/config.yaml` (`visual.environment_frame_ratio: 0.4`,
optional `visual.world_description`).

**Verify:** in a run, ≥40% of the 5 frames are wide world/landscape shots with no
dominant character; first and last frames are establishing shots.

---

## P4 — More characters wanted in scenes

**Problem:** Scenes feel single-character; want more characters present.

**Root cause:** `char_presence` frames usually map ONE dominant character; the prompt
assembler injects only the single highest-weight character's description (`cw >= 0.5`
picks the first match and breaks).

**Fix:**
1. In `utils/story_planner.py` plan prompt: "When the scene involves interaction, include
   2–3 characters in a frame with distinct weights (e.g. protagonist 0.7, mentor 0.5)."
2. In `utils/scene_director.assemble_prompt` usage and the pipeline's character-lock
   injection (`core/pipeline_long.py` ~line 1610 `for c_key, cw in cp.items()`), allow
   **multiple** characters' identity tokens when several have `cw >= 0.4` (currently it
   tends to inject one). Budget tokens so 2 character descriptions fit (identity-first).
3. Keep the per-character seed logic working per-frame (see P6) so each character stays
   consistent even when multiple appear.

**Files:** `utils/story_planner.py`, `core/pipeline_long.py` (multi-character injection),
`utils/scene_director.py` (assemble_prompt multi-identity budgeting).

**Verify:** interaction frames show 2+ distinct characters.

---

## P5 — Style is not the intended Arcane-style 2D semi-realistic

**Problem:** Output doesn't follow the "Arcane / semi-realistic 2D" style.

**Root cause:** The base model is `Lykon/AnyLoRA` (a general anime model) and the style
string mentions Arcane, but (a) the prompt budgeter may trim the style tokens (style is
lowest priority in `assemble_prompt`), and (b) AnyLoRA doesn't naturally produce the
Arcane painterly look without stronger style anchoring or a matching LoRA/model.

**Fix:**
1. **Protect style tokens from being trimmed away.** In `assemble_prompt`, reserve a
   small guaranteed budget for a short STYLE ANCHOR (e.g. first 8–10 style tokens) so
   the look isn't dropped under the 70-token cap. Put a compact style anchor near the
   front: "Arcane style, semi-realistic 2D, painterly, cinematic".
2. **Tighten the config style string** to the essentials that actually steer SD
   (remove redundancy): keep "Arcane-inspired semi-realistic 2D painterly animation,
   cinematic lighting, volumetric shadows". Remove "8k quality" (does little, costs
   tokens).
3. **Recommend a matching model/LoRA (optional, operator step):** AnyLoRA won't fully
   match Arcane. Document candidates the operator can switch `sd_model_path` to (e.g. an
   Arcane-style LoRA on top of AnyLoRA, or a semi-realistic checkpoint). Add an
   `image_gen.style_lora_path` slot that, if set, is loaded as a fused style LoRA
   (reuse the acceleration fuse pattern). Default empty (no behavior change).
4. **Negative prompt**: ensure it doesn't fight the style (it lists "3d render" — good;
   keep "photorealistic" only if style is 2D, which it is).

**Files:** `utils/scene_director.py` (style anchor + budget), `config/config.yaml`
(trimmed style string, `image_gen.style_lora_path: ""`),
`video/image_gen/image_gen.py` (optional style LoRA load, mirrors accel fuse).

**Verify:** generated frames show a consistent painterly semi-realistic 2D look across
all 5 images.

---

## P6 — Protagonist's face is consistent, but eye color / hair / clothes change per image

**Problem:** Same face (LoRA/seed working) but secondary traits (eye color, hair color,
clothing) drift between images.

**Root cause:** The per-character **seed** is applied (B5) so the face is stable, but the
**character description tokens** that specify eye/hair/clothes are NOT consistently
injected into every frame featuring that character. In `core/pipeline_long.py` the
injection only appends the description when `cw >= 0.3` AND when not already present, and
`enrich_prompts` may strip/rewrite character tokens for some frames. So some frames have
the full "striking eyes, dark clothing" description and others don't → SD invents new
eye/hair/clothes each time.

**Fix:**
1. **Make the character's full visual description MANDATORY in every frame where that
   character has `cw >= 0.4`.** Centralize this: build a canonical per-character token
   string ("<name>, <eye>, <hair>, <clothing>, <build>") and always prepend it
   (identity-first) for frames featuring the character. Do NOT let `enrich_prompts`
   strip it for mid/high-weight frames (only strip for env frames < 0.3).
2. **Tighten the character descriptions in config** so traits are explicit and specific
   (the current "striking eyes" is vague → SD picks random colors). Give concrete locked
   traits, e.g. "warm brown eyes, short black hair, dark grey coat". Lock these as the
   canonical identity tokens.
3. **Keep the deterministic seed per character** (already implemented) — combined with
   explicit, always-present trait tokens, eye/hair/clothes will stop drifting.
4. Add the strongest drift-prone traits to the **negative prompt dynamically** is not
   needed; explicit positives + seed are the right fix.

**Files:** `config/config.yaml` (specific, locked character trait descriptions),
`core/pipeline_long.py` (always-inject canonical identity tokens for cw≥0.4 frames),
`utils/scene_director.py` (never strip identity tokens on non-env frames).

**Verify:** across the 5 images, the protagonist has the SAME eye color, hair, and
clothing in every frame where present.

---

## P7 — (supporting) Honor images-per-segment and environment ratio together

When forcing the environment-frame ratio (P3), keep the total image count equal to the
locked `images_per_segment` (5). Adjust `char_presence` in place; never change the count.

**File:** `core/pipeline_long.py`. **Verify:** still exactly 5 images, ~2 of which are
world/environment shots.

---

## Suggested order & batching

1. **P0** (assembly timeout + light Ken Burns) — unblocks getting ANY finished video.
2. **P1** (length per mode) — makes videos ~1 min so P0 is comfortable.
3. **P6 + P4** (character traits always injected; multi-character) — visual identity.
4. **P3** (environment/world frames) — composition.
5. **P5** (style anchor + optional style LoRA) — look.
6. **P2** (modern Hindi) — audio register.

After each: run `pytest -q`, `getDiagnostics` on changed files, keep pipeline importable.
Then one full `--file` validation run (expect ~1-min video, 5 images with ~2 world
shots, consistent protagonist, modern Hindi narration, finished MP4).

## Definition of done
- A `--file` run produces a COMPLETE MP4 (no assembly timeout).
- Video length ≈ requested (~1 min for 140 words).
- Narration is modern conversational Hindi.
- Of 5 images: ≥2 are world/environment shots; interaction frames show 2+ characters;
  protagonist's eyes/hair/clothes are identical across frames; look is consistent
  Arcane-style semi-realistic 2D.
- All tests pass; no diagnostics.
