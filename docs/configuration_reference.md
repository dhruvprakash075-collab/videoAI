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
| `tts` | `engine` | `"omnivoice"` | Primary TTS; edge-tts is fallback |
| `script` | `words_per_segment` | `100` | Was 130 — stale in old docs |
| `performance` | `staged_loop` | `true` | C1 staged loop enabled |
| `performance` | `vram_sd_threshold_gb` | `4.5` | Min VRAM before SD loads |
| `performance` | `vram_evict_wait_s` | `15` | Polling timeout after eviction |
| `audio_fx` | `enabled` | `true` | Only `thunder.wav` SFX bundled |
| `whisper_model` | — | `"tiny"` | Subtitle alignment (fast pass) |
| `whisper_model_final` | — | `"base"` | Final subtitle render |
| `loudnorm_two_pass` | — | `true` | Two-pass Loudnorm enabled |
| `target_lufs` | — | `-14` | LUFS target for mastering |

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

Preset rendering anchors applied to Stable Diffusion prompts. The [style_resolver.py](file:///c:/Video.AI/style_resolver.py) (3-layer resolver) picks the matching preset at generation time.

**Format example**:
```yaml
styles:
  cinematic:
    positive: "cinematic style, 8k resolution, photorealistic, dramatic lighting"
    negative: "cartoon, anime, drawings, low quality, text, watermark"
```

---

## 4. Adding / Changing Config

1. Add key to `config/config.yaml`.
2. Add a matching Pydantic field to `config/config_schemas.py`.
3. Read in code via `config.get("section", {}).get("key", default)` — **never hardcode values**.
