# Configuration Reference

The system is configured entirely via YAML files. All live values are in `config/config.yaml` — **never trust any other doc if it conflicts with the YAML file**.

---

## 1. Parameters (`config/config.yaml` — 292 lines)

Schema-validated at startup by `config/config_schemas.py` (Pydantic). Unknown keys won't crash (schemas use `extra='allow'`) but lose validation.

### Key Sections & Ground-Truth Values

| Section | Key | Live Value | Notes |
|---|---|---|---|
| `models` | `director` | `"hermes-director"` | Planning / translation LLM |
| `models` | `writer` | `"zephyr-writer"` | Script generation LLM |
| `tts.omnivoice` | `num_step` | `16` | Was 24 — reduced for speed |
| `tts` | `engine` | **"supertonic"** | **Active default (2026-06-04)**. Fallback chain: `supertonic → omnivoice → edge-tts` |
| `tts.supertonic` | `voice` | `"character_voices/dhruv_voice_polished.json"` | DIY extract, 18s polished, loss 0.2721 |
| `tts.supertonic` | `steps` | **16** | Was 8 — A/B winner 2026-06-04 (less hiss) |
| `tts.supertonic` | `speed` | **1.0** | Was 1.05 — A/B winner 2026-06-04 (natural pace) |
| `tts.supertonic` | `silence_duration` | **0.1** | Was 0.3 — modern snappy |
| `tts.supertonic` | `max_chunk_length` | `150` | Force chunking before danda fix (P6-1) |
| `script` | `words_per_segment` | `100` | Was 130 — stale in old docs |
| `performance` | `staged_loop` | `true` | C1 staged loop enabled |
| `performance` | `vram_sd_threshold_gb` | `4.5` | Min VRAM before SD loads |
| `performance` | `vram_evict_wait_s` | `15` | Polling timeout after eviction |
| `audio_fx` | `enabled` | `true` | Only `thunder.wav` SFX bundled |
| `whisper_model` | — | `"tiny"` | Subtitle alignment (fast pass) |
| `whisper_model_final` | — | `"base"` | Final subtitle render |
| `loudnorm_two_pass` | — | `true` | Two-pass Loudnorm enabled |
| `target_lufs` | — | `-14` | LUFS target for mastering |

### TTS engine fallback chain (2026-06-04)

When `tts.engine == "supertonic"` and synthesis fails (e.g. ONNX OOM,
crashed worker, missing voice JSON), `audio/audio_proxy.py::tts_generate()`
auto-tries:
1. **supertonic** (active) — CPU ONNX, 4.5x faster than OmniVoice
2. **omnivoice** — GPU DiT, 1.2x realtime, richer timbre
3. **edge-tts** — Cloud-free Azure neural TTS, last resort

The chain mirrors the F5 fallback pattern. To force a specific engine,
set `tts.engine` directly.

### DIY voice style JSONs (Supertonic 3)

Only the active default is present on disk. Use `external/supertonic_embed/`
to extract additional profiles from reference audio.

| File | Source | Loss | Status |
|---|---|---|---|
| `dhruv_voice_polished.json` (285KB) | Generic placeholder (F1 profile) | — | **ACTIVE default** — replace with real extract |
| `dhruv_voice_v3_9s.json` | 9s raw auto-trim — **missing, needs extraction** | 0.2399 | Backup — see `docs/voice_cloning.md` |
| `dhruv_voice_v3.json` | 71.94s merged — **missing, needs extraction** | 0.2388 | Empirical ceiling reference |

To switch or extract: edit `tts.supertonic.voice` in `config/config.yaml`.
See `docs/voice_cloning.md` for extraction commands.

### v6 Pipeline Sections
- **`source:`** — Source ingestion config (v6 Phase 1): max file size, allowed extensions.
- **`research:`** — Web research config (v6 Phase 3): sources (`wikipedia`, `wikimedia`, `rss`), budget cap (default 3), per-source word limit.
- **`critic:`** — Quality gate config (v6 Phase 4): approval threshold (default 60/100), max rewrite attempts.
- **`seo:`** — YouTube SEO config (v6 Phase 5): tag count, hashtag count, chapters.
- **`checkpoint:`** — Resume-on-crash config: checkpoint directory path.
- **`memory:`** — Story memory and world state options.

---

## 2. Prompts (`prompts.yaml`)

Holds all LLM system prompts and task templates:
- **`critic`**: The 5-dimension script evaluation rubric (Hook, Emotional Arc, Pacing, Retention, TTS-friendliness — 20pts each = 100 total, approved at ≥ 60).
- **`director`**: Story structuring, cliffhanger logic, character weight resolution, and narrator suggestions.
- **`writer`**: Script expansion, Devanagari translation parameters, and tone/style guidance.

---

## 3. Visual Styles (`styles.yaml` + `style_resolver.py`)

Preset rendering anchors applied to Bonsai image prompts. The [style_resolver.py](file:///c:/Video.AI/style_resolver.py) (3-layer resolver) picks the matching preset at generation time.

> **2026-06-04:** `negative` fields are accepted for back-compat with old
> prompts but Bonsai ignores them (FLUX-style models do not use negative
> prompts).

**Format example**:
```yaml
styles:
  cinematic:
    positive: "cinematic style, 8k resolution, photorealistic, dramatic lighting"
    negative: "cartoon, anime, drawings, low quality, text, watermark"  # ignored by Bonsai
```

---

## 4. Image Generation (`image_gen` block, 2026-06-04)

Bonsai 4B ternary + IP-Adapter FLUX v2 is the only image backend. No
Stable Diffusion, no LoRA — character face consistency is via
IP-Adapter referencing per-character master portraits.

```yaml
image_gen:
  backend: "bonsai"                              # always "bonsai" — no other backends
  bonsai_model: "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"
  height: 1024
  width: 1024
  steps: 4                                       # Bonsai is distilled; more steps is slower, not better
  guidance_scale: 3.5                            # 3.0–4.0 sweet spot; <3 loose, >4 oversaturated
  ip_adapter_scale: 0.8                         # 0.0–1.0; balance between prompt adherence and face lock
  lock_seed: true                                # same seed + same prompt = same image
  preview_steps: 4                              # preview renders use this step count
  oom_recovery: true                             # 2-tier ladder (see runtime_safety_guide.md §4)
  upscaler: { model: "none", model_path: "", scale: 4 }  # opt into Real-ESRGAN if needed

  # Character portrait generation (lazy, on first frame with char_presence ≥ 0.3)
  # No "negative_prompt" — FLUX-style models do not use them
  # No "lora_*" — LoRA removed; consistency is via IP-Adapter
  # No "xformers" / "channels_last" / "cpu_offload" — sequential VRAM keeps peak ~3.5GB
  # No "acceleration" — Bonsai is already distilled and fast
```

**Character data** in the Director overlay may include an optional
`portrait_prompt` field. If absent, `visual_description` is prefixed
with `"portrait, "` and used as the prompt. Master portraits are
stored at `studio_projects/{project_id}/characters/{char_key}/master.png`.

**OOM report** is written to
`studio_outputs/{project}/oom_report.json` and accessible via
`image_gen.get_oom_report()`. The frame cache key includes
`master_portrait_hash` so portrait regen invalidates stale frames.

---

## 5. Adding / Changing Config

1. Add key to `config/config.yaml`.
2. Add a matching Pydantic field to `config/config_schemas.py`.
3. Read in code via `config.get("section", {}).get("key", default)` — **never hardcode values**.
