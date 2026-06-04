# What You Can Add to Video.AI: FramePack Motion

*Focus: Local, 6GB VRAM image-to-video motion generation*

---

## 🔴 FramePack (Image-to-Video Motion)

**What:** Next-frame prediction that turns static Bonsai-generated images into 30fps video clips.

**Why it matters:** 
- Adds natural movement (drifting fog, flickering flames, character motion) from a single generated frame.
- **Zero VRAM leaks**: Explicitly designed for 6GB consumer GPUs using O(1) memory scheduling.
- **Ready in codebase**: Code already exists in the project at [framepack_i2v.py](file:///c:/Video.AI/video/image_gen/framepack_i2v.py).
- **Compatible with IP-Adapter output (2026-06-04)**: FramePack consumes the same per-character master portrait that IP-Adapter uses, so motion and consistency share state.

**How to activate:**
1. Install dependencies:
   ```powershell
   pip install framepack
   ```
2. Download model weights:
   ```powershell
   huggingface-cli download lllyasviel/FramePack --local-dir hf_cache\framepack
   ```

---

## ✅ Already Shipped: Supertonic 3 TTS + DIY Voice Clone (2026-06-04)

> Supertonic 3 was the previous "research" item. It is now **the default TTS engine** (was OmniVoice). See `supertonic_pipeline.md` and `voice_cloning.md` for full detail.

**Why it matters:**
- **4.5x faster than OmniVoice** end-to-end (5.1x sustained on RTX 4050).
- **Zero VRAM pressure** — CPU ONNX, frees the 6GB GPU for Bonsai.
- **31 languages** including hi, en, ko, ja, zh, es, fr, de, etc.
- **Free DIY voice cloning** via `external/supertonic_embed/` (MIT) — same output format as Supertone's paid Voice Builder, zero cost.

**Production speed (verified 2026-06-04):**
- 1-min Hindi narration: 33.6s synthesis (3.0x realtime)
- 3-hour video TTS: 30 min wall time
- Image gen is now the bottleneck, not TTS

**Key learnings (2026-06-04):**
- **Emotion tags (3: `<laugh>`, `<breath>`, `<sigh>`) are no-ops** on Hindi — kept out of defaults
- **More training audio does NOT improve voice quality** — 12,800 floats is the info ceiling. 9s of clean audio is enough.
- **Polished 18s ≠ better loss but sounds cleaner** (perceptual A/B) — kept as default anyway
- **P6-1 danda fix required** for any Hindi text with `।` (upstream chunker bug)
- **P6-2 PYTHONIOENCODING=utf-8 required** in all worker spawns on Windows

---

## ✅ Already Shipped: Bonsai 4B + IP-Adapter FLUX v2 (2026-06-04)

> Stable Diffusion 1.5 + LoRA face-lock was the previous "research" item
> (Tier 3 IP-Adapter). It is now **the only image backend**. LoRA training
> has been removed; character consistency is via IP-Adapter FLUX v2
> referencing per-character master portraits.

**Why it matters:**
- **FLUX-quality output** on 6GB VRAM (was SD 1.5 quality only).
- **No LoRA training** — characters are described in `portrait_prompt`,
  not learned. Saves ~10 min/character of training time per project.
- **Lazy master portrait gen** — 3 candidates + CLIP pick the best; only
  fires on the first frame in a project where a character appears.
- **Cross-project consistency** — same `project_id` → same master
  portrait → same face across all future videos for that project.

**Production speed (verified 2026-06-04):**
- 4 imgs/segment × 90 segments: ~30 min wall time on RTX 4050
- Image gen dominates total wall time now (TTS is CPU, was the bottleneck before)

**Key learnings (2026-06-04):**
- **Bonsai is sequential VRAM only** — `enable_model_cpu_offload()` is
  wrong for 4B; peak is ~3.5GB on 6GB card. No offload needed.
- **Bonsai ignores `negative_prompt`** — FLUX-style models do not use them.
- **IP-Adapter scale 0.8** is the balanced default; <0.6 face drifts, >0.9
  the prompt becomes a face morph.
- **Dominant-character rule** — only the top-weight character (≥0.3) gets
  IP-Adapter reference; secondary chars get prompt description only.
  Multi-ref IP-Adapter is unsupported in FLUX v2 today.

**Cost of the change:**
- `train_lora.py` deleted (replaced by lazy master portrait).
- `tests/test_train_lora.py`, `tests/test_oom_ladder.py`, `tests/test_image_accel.py` deleted.
- `tools/ab_compare_t2i.py` retained (deliberately — it's the A/B benchmark
  for SD 1.5 vs Bonsai; not production code).

---

## Future research items

### Tier 1 (next 1-2 weeks)
- **DMD2/LCM acceleration** for Bonsai — 50-70% speedup, near-zero quality loss
- **Real-ESRGAN** upscaler — replaces Lanczos for crisper 1080p output (currently 30-50% slower)

### Tier 2 (next 1-2 months)
- **FramePack** (above) — real I2V motion
- **Music/soundtrack generation** — MusicGen, AudioLDM, etc.
- **Per-language voice JSONs** — extract English, Tamil, etc. voices
- **Multi-reference IP-Adapter** — when FLUX v3 supports multiple image refs

### Tier 3 (research)
- **Stronger emotion/prosody control** — investigate Soft-VC or hierarchical prosody
- **Voice acting director** — LLM-driven intonation/pacing control
- **Bonsai-binary (1-bit) benchmark** — if `prism-ml/bonsai-image-binary-4B-gemlite-1bit`
  becomes available on HF, A/B against ternary for memory/quality tradeoff.
