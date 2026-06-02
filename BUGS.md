# Video.AI — Production Phase Bug & Issue Catalog

Generated from a deep audit of the production phase (TTS, visual generation, rendering,
agents, memory, config). Grouped by severity and theme. Each entry: location, what's
wrong, impact, and a suggested fix direction.

Severity legend:
- **P0 — Breaks output quality** (user-visible defects in the final video)
- **P1 — Feature silently disconnected** (a built feature does nothing)
- **P2 — Reliability / correctness risk** (can hang, corrupt, or mislead)
- **P3 — Originality / compliance** (IP leakage; user requires original content)
- **P4 — Minor / cosmetic / dead code**

A recurring theme: several features (emotion, mood-pacing, Devanagari, character
consistency) are **built but disconnected at the seams** — the wiring drops them.

---

## P0 — Breaks output quality

### B1. Subtitles are English while narration is Devanagari (mismatch)
- **Where:** `core/pipeline_long.py` ~line 1574 — `render_with_assets(..., script=script, ...)`
- **Problem:** `script` is the English script. The audio uses `devanagari_script`. The
  renderer burns English captions over Hindi voice-over.
- **Impact:** Every video has subtitles that don't match the spoken words.
- **Fix:** Pass the Devanagari script (the same text sent to TTS) to the renderer for
  subtitle generation; pass `word_timestamps_json` through too.

### B2. Word timestamps computed but never used by the renderer
- **Where:** `core/pipeline_long.py` — `word_timestamps_json` is produced and
  checkpointed, but `render_with_assets(...)` is never given it.
- **Impact:** TikTok-style word-synced subtitles fall back to even time-splitting →
  captions drift out of sync with speech.
- **Fix:** Add `word_timestamps_json` param to `render_with_assets` → `build_html` /
  `create_segment_mp4` (assembler already accepts it).

### B3. Image resolution mismatch → soft/blurry 1080p output
- **Where:** `config/config.yaml` `image_gen.width/height = 768x432` vs
  `video.resolution = 1920x1080`; renderer/assembler build a 1080p canvas.
- **Problem:** Images are upscaled ~2.5× before display.
- **Impact:** Visibly soft frames on a 1080p YouTube upload.
- **Fix (needs VRAM decision):** Generate at higher native res (e.g. 960×540) + a
  high-quality upscale pass, or render at the native image res. Tradeoff on 6GB GPU.

### B4. CLIP 77-token truncation drops character descriptions
- **Where:** `utils/scene_director.enrich_prompts` + `core/pipeline_long` prompt
  assembly + `specialized_models.generate_image_prompt`.
- **Problem:** Final prompt = base + camera + style + "8k masterpiece" + image-engineer
  paragraph + appended character description. Easily 100+ tokens; SD's CLIP truncates at
  77, and the character description (appended last) is what gets cut.
- **Impact:** Directly undermines character face consistency — the lock text is dropped.
- **Fix:** Budget the prompt; put character identity tokens FIRST; trim camera/style
  boilerplate; consider compel/long-prompt weighting.

### B5. Per-character fixed seed never applied
- **Where:** `video/image_gen/image_gen._stable_diffusion` — no `generator`/seed passed
  to the pipeline call.
- **Problem:** The new visual-lock `seed` (in ProjectStore) is stored but unused.
- **Impact:** Character faces drift between images/segments/runs.
- **Fix:** Read the per-character seed from the visual lock; pass
  `generator=torch.Generator(device).manual_seed(seed)`.

### B6. Non-anime prompt contradicts its own negative prompt
- **Where:** `utils/scene_director.enrich_prompts` — non-anime branch adds
  `photorealistic, masterpiece`, while the negative prompt lists `photorealistic` to
  avoid.
- **Impact:** Conflicting guidance for realistic styles (your anime style dodges it).
- **Fix:** Make positive/negative style tokens mutually consistent per style.

### B7. Hyperframes caption font can't render Devanagari
- **Where:** `video/renderer/renderer.build_html` — `font-family:Inter,sans-serif`.
- **Problem:** Inter lacks Devanagari glyphs → tofu/boxes if Hyperframes path is used.
- **Fix:** Use Noto Sans Devanagari (or a bundled Devanagari font) in the HTML caption
  style.

---

## P1 — Built features that are silently disconnected

### B8. Emotion injection discarded for Hindi (the default)
- **Where:** `core/pipeline_long.py` ~1338–1343 — `script_for_tts =
  inject_emotion(...)` then immediately `if devanagari_script: script_for_tts =
  devanagari_script` (overwrites).
- **Impact:** `inject_emotion` never affects the Hindi narration that's actually used.
  The emotional-delivery feature is off for your real output.
- **Fix:** Apply emotion shaping to the Devanagari text (Devanagari-aware), or inject
  emotion before translation and have the translator preserve the markers.

### B9. Mood-based TTS rate (`get_mood_rate`) never used
- **Where:** `utils/emotion_control.get_mood_rate` — no caller.
- **Impact:** Every segment speaks at the static `tts.omnivoice.speed`, ignoring mood.
- **Fix:** Pass `get_mood_rate(mood)` into `tts_generate` → omnivoice `--speed`.

### B10. `inject_emotion` is Latin-only; useless on Devanagari
- **Where:** `utils/emotion_control._safe_ellipsis` regex `(?<=[a-zA-Z])\.` and
  `". " → "! "` replacements.
- **Impact:** Even if B8 is fixed, the function does nothing meaningful on Devanagari
  (which uses `।`).
- **Fix:** Add Devanagari sentence-boundary handling (`।`, `?`, `!`).

### B11. Director/Writer-planned `num_images` partially honored
- **Where:** `build_prompts` respects `plan['num_images']` (good), but the count can be
  overridden by enrich/merge steps and the SD call renders whatever prompts exist.
- **Impact:** Pacing intent (more cuts for action) can be diluted.
- **Fix:** Treat `num_images` as authoritative through to `generate_images`.

### B12. RVC / SFX / music all disabled by default
- **Where:** `config.yaml` — `rvc.enabled: false`, `audio_fx.enabled: false`,
  `music.enabled: false`.
- **Impact:** Voice conversion, sound effects, and background music never run unless
  toggled. (Mastering DOES run unconditionally — that part is fine.)
- **Fix:** Decide intended defaults for YouTube; document them.

---

## P2 — Reliability / correctness risks

### B13. `RuntimeError` retried up to 50× (deterministic-failure hang)
- **Where:** `utils/retry_manager` — `RETRYABLE_EXCEPTIONS` includes `RuntimeError`,
  `MAX_RETRIES=50`, applied to `generate_images`/`tts_generate`.
- **Impact:** A deterministic failure retries ~50× with backoff → up to ~50 min hang
  per segment before failing.
- **Fix:** Separate transient (connection/timeout) from deterministic errors; cap
  non-network retries at 2–3.

### B14. Double retry on image OOM
- **Where:** `generate_images` has internal 3-tier OOM handling AND is wrapped by the
  50× retry decorator.
- **Impact:** OOM handled twice → compounds B13.
- **Fix:** Don't wrap `generate_images` with the outer retry, or make OOM
  non-retryable at the outer layer.

### B15. CrewAI kickoff without the serialization lock in context compression
- **Where:** `utils/context_manager._llm_compress` calls `crew.kickoff()` with no
  `_crewai_lock`, while the rest of the pipeline serializes every kickoff.
- **Impact:** Concurrent kickoffs can corrupt CrewAI's single-threaded executor (the
  exact failure `_crewai_lock` exists to prevent).
- **Fix:** Acquire the shared lock (or route compression through the same serialized
  path).

### B16. OmniVoice reloads the full model every segment
- **Where:** `audio/omnivoice_worker.py` — fresh subprocess + `from_pretrained` per
  call.
- **Impact:** For a 90-segment video, 90 model loads — minutes of pure overhead.
- **Fix:** Persistent worker process (load once, stream segments), or batch.

### B17. `review_script_fast` JSON regex can't match nested objects
- **Where:** `utils/specialized_models` — fallback regex `\{[^{}]+\}`.
- **Impact:** Nested reviewer output → parse fail → silent auto-approve. Reviews weaker
  than they appear.
- **Fix:** Use a brace-depth parser (the Director already has `_parse_json`).

### B18. Wikipedia include/skip section lists conflict
- **Where:** `utils/web_search` — `"history"` in SKIP matches `"publication history"`
  (in INCLUDE) via substring check; `"premise"` duplicated in INCLUDE.
- **Impact:** Legitimate sections dropped; minor noise.
- **Fix:** Use exact section matching or order include-before-skip.

### B19. Hyperframes renderer hardcodes environment
- **Where:** `video/renderer/renderer.render_html` — `wsl -d Ubuntu -u dhruv`,
  `cd /mnt/c/Video.AI`.
- **Impact:** On any other machine, distro, user, or non-C: drive → Hyperframes always
  fails → silent fallback to assembler every segment.
- **Fix:** Derive distro/user/path from env/config; detect WSL availability.

### B20. Cache-key default params don't match config
- **Where:** `image_gen._prompt_cache_key` defaults (1024×576/25/7.5) vs config
  (768×432/12/6.0).
- **Impact:** If a key is missing, cache keys diverge → spurious misses/collisions.
- **Fix:** Pull defaults from the same source as the real generation params.

### B21. `audio_chunk_threshold=30s` can seam voice-clone timbre
- **Where:** `audio/omnivoice_worker.py`.
- **Impact:** Segments > ~30s of audio get chunked; chunk boundaries can shift cloned
  voice timbre.
- **Fix:** Tune threshold for your segment length; crossfade chunk seams.

### B22. Quality check duration tolerance vs locked durations
- **Where:** `utils/quality_check.check_video` — 20% duration tolerance vs
  `config.video.total_duration_min`.
- **Impact:** With the new DecisionRecord-driven durations, expected vs actual may flag
  false failures.
- **Fix:** Compare against the resolved decision-record duration, not raw config.

---

## P3 — Originality / IP leakage (you require original content)

### B23. Named IP characters shipped in config
- **Where:** `config/config.yaml` and `config/config.py._default_config` — "Lumian Lee"
  & "Klein Moretti" (Lord of Mysteries), "Kiana Kaslana" (Honkai Impact). Keyword
  "beyonder", "honkai".
- **Fix:** Replace with original placeholder characters; purge franchise terms.

### B24. Hardcoded IP-name fallbacks in code
- **Where:** `utils/utils.build_prompts` (`next(iter(...), "lumian_lee")`),
  `utils/local_ui.upload_voice` (`character_name="lumian_lee"`),
  `agents/executive_agent.execute_voice_over` (`character="lumian_lee"`),
  `agents/director_agent.UIState.character = "lumian_lee"`,
  `memory/memory.py` docstring ("Lumian's chest").
- **Fix:** Generic fallback (`"narrator"`/`"protagonist"`).

### B25. IP description leaks into image-engineer few-shot example
- **Where:** `utils/specialized_models.generate_image_prompt` — example output
  "young man with jet-black hair and blue eyes" (the Lumian look).
- **Impact:** Biases every generated image prompt toward that IP character's appearance.
- **Fix:** Replace example with a neutral, original description.

### B26. Web research imports IP canon
- **Where:** `utils/web_search.search_story_web` on a known franchise topic pulls that
  IP's characters/world from Wikipedia.
- **Impact:** For "original content only", researching a franchise actively imports it.
- **Fix:** Gate/skip web research (or restrict to generic craft) when original-content
  mode is set.

### B27. Test fixtures use IP name
- **Where:** `tests/test_project_store.py` uses "Lumian Lee" as sample data.
- **Fix:** Use an original placeholder in tests.

---

## P4 — Minor / dead code / drift

- **B28.** `core/main.create_executive` default model `"llama2:7b"` (not pulled);
  misleading "re-using writer model" comment.
- **B29.** `retry_manager` `urlopen`-name branch is dead (decorator never wraps
  urlopen).
- **B30.** `compatibility.apply_all_patches()` runs at import AND explicitly (guarded,
  but imports torch/diffusers each fresh interpreter).
- **B31.** `compatibility.setup_compatibility` filters `langchain_core` warnings —
  langchain no longer used per its own docstring.
- **B32.** `audio_fx._DEFAULT_SFX` references 9 missing files (only `thunder.wav`
  exists) — silent no-ops.
- **B33.** `renderer._CHROME_PATH` defined but unused.
- **B34.** `web_search._DDG_API` constant unused (only `_DDG_HTML` used).
- **B35.** `specialized_models` hardcodes `num_ctx: 16384` (not config-driven; may
  truncate or waste RAM depending on model).
- **B36.** OmniVoice fallback defaults in `audio_proxy` (`num_step=32`, `gs=2.0`,
  `speed=0.95`) differ from `config.yaml` (`40`/`2.5`/`0.85`) — config wins but drift
  invites confusion.
- **B37.** `config_schemas.py` (`words_per_segment` default 390) vs `config_schema.py` /
  `config.yaml` (130) — inconsistent defaults across the two schema files.
- **B38.** Romanized Hinglish translation path (`audio_proxy.translate_hinglish`,
  `engine="edge"`) conflicts with the Devanagari preference; effectively a second,
  divergent translation path.
- **B39.** `enrich_prompts` name-stripping by regex substitution is fragile for
  multi-word/original names (can mangle prompts).
- **B40.** `build_html` splits captions by word count, not audio timing (compounds B2).

---

## Cross-cutting recommendation

The P0/P1 cluster is the priority — they're why a finished video looks/sounds worse than
the code's features suggest. The cleanest path is a dedicated **production-quality-fixes
spec** that:
1. Restores the audio chain (B8, B9, B10, B16, B21) — emotion + mood-pacing + Devanagari.
2. Fixes the visual identity chain (B4, B5, B6, B11) — prompt budget + fixed seed.
3. Fixes subtitle correctness (B1, B2, B7, B40).
4. Hardens reliability (B13, B14, B15, B19).
5. Purges IP / enforces originality (B23–B27).

Items needing an explicit design decision (VRAM/perf tradeoff): B3 (resolution),
B16 (persistent TTS worker), B19 (renderer portability).
