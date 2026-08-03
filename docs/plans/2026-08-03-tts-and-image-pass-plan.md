# Video.AI Optimization Plan — TTS fixes + image-pass split

Date: 2026-08-03. Basis: real 1-seg run (73.2s video, 1063.6s wall, QC PASS) + live
root-cause verification. Every step has a verification gate.

Status: **Part A DONE (2026-08-03)** — the gate at A2.6 passed: full 1-seg run
logged `[IndicF5] Calling one-shot worker...` → `TTS generated: indicf5_*.wav`,
no supertonic degradation. **Part B DONE (B1-B5) — all three measurement runs
succeeded on 2026-08-03 evening; measurements below.**

> NOTE: A2's original "GatedRepoError 401" diagnosis was superseded mid-plan:
> after auth was fixed the real blocker was transformers 5.x wrapping
> `from_pretrained` model init in a `torch.device("meta")` context
> (modeling_utils.get_init_context) that crashes IndicF5's eager Vocos init, plus
> `torch.compile` missing `torch._thread_safe_fork` on this build and a
> `load_model` ckpt_path signature mismatch. Fixed operationally in
> `external/IndicF5/run_indic.py:_load_model` (direct construction of the cached
> remote module, identity torch.compile, ckpt injection) — see
> `docs/session-2026-08-03.md`.

---

## Part A — TTS

### A1. IndicF5 fallback root cause (CONFIRMED, live) + why output regressed

**Regression story (verified):** the "good" output ~1 week ago was **IndicF5,
not supertonic**. Evidence:
- `external/IndicF5/tts_output/test_indicf5_output*.wav` exist, dated
  **2026-07-27 17:15/17:19** — IndicF5 ran successfully that day.
- Commit `6b4b8a907` (2026-08-02, audit-fix) flipped
  `config/config.yaml:20` `engine: indicf5` → `engine: supertonic` — a
  workaround for the fallback, made permanent. Every run since has been
  supertonic-only. That is the user-perceived regression.

**Why IndicF5 fails now (live-verified):**
- Chain: `tts.engine: indicf5` → `audio_proxy._call_indicf5_worker` →
  `indicf5_worker.py` → `external/IndicF5/run_indic.py:68`
  `AutoModel.from_pretrained("ai4bharat/IndicF5", trust_remote_code=True)` →
  **`huggingface_hub.errors.GatedRepoError: 401`** → worker returns
  `{"status": "error"}` → `audio_proxy.py:961` "degrading to supertonic".
- `ai4bharat/IndicF5` is a **gated HF repo** (per HF docs: "You need to agree
  to share your contact information to access this model") — reproduced 401
  with both PATH python 3.14 and `venv` python.
- **No auth anywhere on this machine**: no `~/.cache/huggingface/token`, no
  `~/.huggingface/token`, no `.env`, no `HF_TOKEN` env, no PowerShell profile.
- **No IndicF5 weights on disk**: no `model.safetensors` anywhere (searched
  `C:\Video.AI` recursively); `hf_cache/hub` holds only faster-whisper-base
  (141 MB). The 07-27 download/cache is gone (wiped by the 08-02 deep-clean's
  "caches" pass, or never persisted).
- No `.env` / dotenv loader exists anywhere in the repo — `HF_TOKEN` must be a
  real environment variable (worker inherits `os.environ`,
  `indicf5_worker.py:41`).
- `HF_HOME` is already `C:\Video.AI\hf_cache` — model will cache there once
  auth works.

**Why supertonic sounds worse (diagnosis — user was right, NOT a steps
problem):**
- Official benchmark (`~/.cache/supertonic3/img/metrics/s3_vs_measured_wer_range_voxcpm2.png`):
  **Hindi is Supertonic 3's worst-tier language — 5.34% CER** (English 2.06
  WER, German 0.86). Only Finnish is comparably bad. This is the model's
  ceiling, not a config knob.
- Voice JSON is fine: `character_voices/dhruv_narration.json` was optimized
  (2026-06-20, supertonic_embed `optimize_style.py`, 200 steps, early-stop
  threshold 0.24) from the same Hindi reference WAV; converges closest to M1
  (cos 0.994, l2 0.752); v2 and v3 preset style spaces are identical, so no
  v2→v3 style mismatch. `lang=hi` is passed correctly (`audio_proxy.py:363`).
- Supertonic 3 = 99M-param CPU ONNX multilingual model (31 langs); IndicF5 =
  GPU F5-TTS fine-tuned for Hindi by AI4Bharat (40 NFE steps, zero-shot
  cloning). Multilingual-small vs Hindi-specialized. Tuning supertonic steps
  (16→32) buys almost nothing; restoring IndicF5 is the fix.

### A2. Fix — operational, zero code
| # | Step | Verify |
|---|------|--------|
| 1 | Log into HF, accept license (gated repo, auto-approve): `https://huggingface.co/ai4bharat/IndicF5` | repo page shows "Access granted" |
| 2 | Create read token: `https://huggingface.co/settings/tokens` | token copied |
| 3 | Persist user env var: `[Environment]::SetEnvironmentVariable('HF_TOKEN','<token>','User')`, restart shell | `echo $env:HF_TOKEN` |
| 4 | `venv\Scripts\python.exe -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('ai4bharat/IndicF5', filename='model.safetensors'))"` | prints cached path (one-time ~2-4 GB download into `hf_cache`) |
| 5 | `config/config.yaml:20` — `engine: supertonic` → `engine: indicf5` (undoes the 08-02 workaround flip in `6b4b8a907`) | schema still validates (`Literal` includes `indicf5`, config_schemas.py:184) |
| 6 | Real-run smoke: 1-seg run | log shows `[IndicF5] Calling one-shot worker...` then `TTS generated:` with an `indicf5_*.wav`, **no** "degrading to supertonic" line |

No code changes needed: `huggingface_hub` reads `HF_TOKEN` automatically; the
supertonic fallback at `audio_proxy.py:961` stays as the safety net.
Rollback: flip `engine:` back to `supertonic`.

### A3. Supertonic quality (fallback stays useful)

- **Do NOT chase steps** — verified against Supertone's own benchmark: Hindi
  CER 5.34% is the model ceiling; 16→32 steps buys near-nothing.
- Keep `steps: 16`, `max_chunk_length: 150`, `silence_duration: 0.1` as-is
  (chunking is required to stay under the ONNX attention limit).
- It remains the documented emergency fallback only; IndicF5 is the target
  engine. No further supertonic investment.

---

## Part B — Image pass split (FaceDetailer vs upscaler)

Currently one monolithic toggle `image_gen.refine_upscale: true`
(config.yaml:187) runs BOTH passes per frame (~86 s/img measured, 8 imgs =
~10 min = 56% of wall time). Split so each is independently toggleable and
measurable.

### B1. New workflow JSONs (split from `manga_refine_upscale_api.json`)

Keep node ids `"1"`=LoadImage / `"11"`=SaveImage in both so the
`image_gen.py:390` drift guard passes unchanged:
- `config/comfyui/workflows/manga_face_detail_api.json` — nodes 1-8
  (FaceDetailer: 20 steps, denoise 0.4) + SaveImage.
- `config/comfyui/workflows/manga_upscale_api.json` — nodes 1-5, 9-11
  (UltimateSDUpscale: 4x-UltraSharp, 2.5×, 12 steps, denoise 0.2).

### B2. Schema (`config/config_schemas.py` ~line 339, ComfyUI section)

Add: `face_detail: bool = False`, `face_detail_workflow_path`,
`upscale: bool = False`, `upscale_workflow_path`.
Keep `refine_upscale` as back-compat alias: `refine_upscale: true` + new keys
unset → both passes on (existing configs keep working, zero breakage).

### B3. Code (`video/image_gen/image_gen.py:352`)

`_refine_upscale` → `_refine_passes(frames, cfg)`: build the enabled pass list
(face detail first, then upscale), loop frames × passes with the same
per-frame try/except keep-original-on-failure semantics (:402-404). Single
ComfyUI runtime/client reused across passes (already the pattern).

### B4. Tests

- `tests/test_image_gen.py` — parametrize existing refine tests over
  (face on/upscale off), (face off/upscale on), (both), (neither), plus the
  `refine_upscale` alias case.
- `tests/test_comfyui.py:618` — add both new workflow names to the registered
  list.
- Gate: `pytest tests/test_image_gen.py tests/test_comfyui.py -q` + ruff.

### B5. Measure, then decide (config.yaml documentation)

Run 3 × 1-seg real runs (face-only, upscale-only, both) and record per-pass
cost in the `config.yaml:185` comment. Expected from workflow math:
upscale ≈ 60-70 s/img (tiled re-diffusion dominates), face detail ≈ 15-25 s/img.
Then set defaults by data. Candidates if speed matters: `upscale_by` 2.5→2.0
or upscale steps 12→10 in the upscale workflow JSON.

**B5 results (2026-08-03 evening, 1-seg real runs, wall-clock from log
timestamps, 6 base images + TTS ~4.4 min shared):**

| Run | Passes | Video duration | Image-phase notes |
|-----|--------|----------------|-------------------|
| 1 | face-only | 51.7 s | 6 × face_detail ≈ 15-20 s/img |
| 2 | upscale-only | 118.9 s | 6 × upscale ≈ 185 s/img (incl. model load) |
| 3 | both | 59.4 s | upscale then face_detail chain per frame |

Measurements are dominated by the upscale pass (~3 min/img with model warm-up;
face detail ~15-20 s/img). Defaults kept: both passes on
(`face_detail: true, upscale: true`). Upscale-cost reduction deferred — the
workflow itself (4x-UltraSharp 2.5x, 12 steps) is the cost driver, not config
plumbing.

**B5 incident, fixed:** measurement batches hit intermittent Windows child-spawn
failures (`rc=0xC0000142 STATUS_DLL_INIT_FAILED`, ~12 ms, empty stdout) — hit
TTS workers, one-shot engines, and even the trivial `set_passes.py` helper
spawn, always inside a batch chain, never standalone; ~2/7 batches failed.
Not reproducible (40× spawn loop all OK), no event-log entries, cwd/env/
PATH/temp-file all verified clean. Fix: `audio/audio_proxy.py:_run_worker` —
the three one-shot worker spawn sites (`indicf5`, `supertonic`, `omnivoice`)
now retry once on `rc=0xC0000142` after 3 s then 10 s. Both post-fix
measurement runs (2 and 3) spawned first-try. Root cause remains machine-level
intermittent (suspect: 29 h zombie ComfyUI duplicate process tree that was
found and killed, or transient Windows loader behavior).

---

## Rejected / out of scope (with reasons)

- **SDXL native high-res instead of upscale** — meinamix is SD1.5 (512-native);
  native high-res collapses (duplicates/broken anatomy). SDXL swap changes art
  style, orphans the two SD1.5 LoRAs, and risks OOM on 6 GB VRAM (preflight
  floor 4.5 GB). Not worth it.
- **python-dotenv dependency** — a persisted user env var does the same job.
- **Character consistency across panels** (real quality gap spotted in the
  video frames: protagonist appears as ~4 different people) — separate task;
  needs per-character reference images or character-lock prompting, not part
  of this plan.
- **Raising supertonic `max_chunk_length`** — ONNX Mul_13 broadcast crash
  above the attention limit (documented at audio_proxy.py:381-383).

## Order of execution

1. A2 (HF token + license + verify download) — user-operational, 15 min + download.
2. A3 (steps 32) — 1-line config change.
3. B1-B4 (split) — ~2 new JSONs, ~40 lines Python, ~6 test updates.
4. B5 (3 measurement runs) — ~25 min total.
5. Final gate: full `pytest tests/ -q` + ruff + one full 1-seg real run with
   IndicF5 + chosen image passes.
