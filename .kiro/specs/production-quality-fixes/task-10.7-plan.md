# Task 10.7 — Implementation Handoff Plan (Acceleration Adapter)

**Task (from tasks.md):**
> 10.7 Add an acceleration-adapter config slot (`image_gen.acceleration`:
> dmd2/hyper_sd/lcm/none) with step/guidance/sampler overrides; default none;
> DMD2 as first candidate — _Requirements: 12.8_

Read this whole document before editing. This task touches a **hot, GPU-only code path**
(`_stable_diffusion`). It CANNOT be unit-tested without a real GPU + a distillation LoRA
file, so correctness depends on careful code review + a manual on-hardware check. Keep the
default OFF so existing runs are byte-for-byte unchanged.

---

## 0. Background — what "acceleration" means here (layman)

Normal Stable Diffusion takes ~12 steps to draw one image. "Distillation" models (DMD2,
Hyper-SD, LCM) are trained to get a similar result in 4–8 steps → roughly 2–3× faster
image generation. You enable one by loading a small LoRA file and lowering the step count
(and, for these models, the "guidance scale" must also drop to ~1–2, or images come out
over-saturated/broken).

---

## 1. Current state (verified)

File: `video/image_gen/image_gen.py`, function `_stable_diffusion(...)`.

What already exists (do NOT redo):
- **Config slot** in `config/config.yaml` under `image_gen.acceleration`:
  ```yaml
  acceleration:
    type: "none"          # "none" | "dmd2" | "hyper_sd" | "lcm"
    lora_path: ""
    steps: 6
    guidance_scale: 6.0
  ```
- **A load block** at pipeline-build time (search comment `Acceleration adapter (R12.8)`):
  it loads the LoRA with `load_lora_weights(..., adapter_name="_accel")` and, for `lcm`,
  switches to `LCMScheduler`. It logs `[ACCEL] ...`.
- **A step override** in the per-frame loop (search `_accel_cfg = cfg.get("acceleration")`):
  when `type != "none"`, sets `throttled_steps = int(_accel_cfg.get("steps", 6))`.

Two real GAPS that make the current code silently wrong when enabled (this is the job):

### GAP A — the acceleration LoRA gets wiped every frame
Right after the load block, the **LoRA Face-Lock** section calls
`_sd_pipe.unload_lora_weights()` once at start, and per frame calls `set_adapters(...)`.
Because the accel adapter is loaded as a *plain adapter* named `_accel`, the
`unload_lora_weights()` / `set_adapters([...])` calls drop it. Net effect: the speed-up
LoRA is loaded, then immediately discarded → acceleration never actually applies.

### GAP B — guidance scale is never lowered
The config has `acceleration.guidance_scale`, but the inference calls all use
`guidance_scale=cfg.get("guidance_scale", 6.0)` (the NORMAL value). Distilled models need
~1–2. So even if the LoRA survived, images would look wrong at 6.0.

---

## 2. Work to do

### 2.1 Fix GAP A — fuse the acceleration LoRA into the base weights

In the acceleration load block, after `load_lora_weights(..., adapter_name="_accel")`,
**fuse** it so it's baked into the model and survives later adapter swaps:

```python
if _accel_lora and Path(_accel_lora).exists():
    _sd_pipe.load_lora_weights(_accel_lora, adapter_name="_accel")
    try:
        _sd_pipe.fuse_lora(lora_scale=float(_accel.get("lora_scale", 1.0)))
        _sd_pipe.unload_lora_weights()   # clears adapter slot; the fused delta stays in the weights
        log.info(f"[ACCEL] Fused {_accel_type} LoRA into base weights: {_accel_lora}")
    except Exception as _fe:
        log.warning(f"[ACCEL] fuse_lora failed ({_fe}); character LoRAs may interfere")
elif _accel_lora:
    log.warning(f"[ACCEL] LoRA path not found: {_accel_lora} — using step/guidance overrides only")
```

Why fuse: the per-frame face-lock code (`unload_lora_weights()` + `set_adapters`) only
affects *named adapters*. A fused delta is part of the base UNet weights, so it persists
and character LoRAs still load/unload cleanly on top.

Add `lora_scale: 1.0` to the config slot (§3).

> Edge case to verify on hardware: fusing the accel LoRA and THEN loading per-character
> face-lock LoRAs must not error. If `fuse_lora` + later `load_lora_weights` conflicts in
> the installed diffusers version, fall back to NOT fusing and instead include `_accel` in
> every `set_adapters([...])` call (more invasive — only if fuse path fails review).

### 2.2 Fix GAP B — resolve guidance once, use it everywhere

Where `throttled_steps` is set from acceleration, also resolve the guidance scale:

```python
_accel_cfg = cfg.get("acceleration") or {}
_accel_active = (_accel_cfg.get("type") or "none").lower() != "none"
if _accel_active:
    throttled_steps = int(_accel_cfg.get("steps", 6))
    _guidance_scale = float(_accel_cfg.get("guidance_scale", 1.5))
else:
    _guidance_scale = float(cfg.get("guidance_scale", 6.0))
```

Then replace **every** `guidance_scale=cfg.get("guidance_scale", 6.0)` inside the 3-tier
inference block (there are 4 call sites: Tier 1, the triton-retry, Tier 2, Tier 3-CPU)
with `guidance_scale=_guidance_scale`.

> IMPORTANT: there are exactly 4 occurrences. Use search to confirm none are missed, and
> confirm none remain after editing (`grep guidance_scale=cfg.get` → 0 results inside the
> loop; the only `cfg.get("guidance_scale")` left should be the `_guidance_scale` resolver
> and `_prompt_cache_key`).

### 2.3 Keep the cache honest

`_prompt_cache_key` already includes `steps` and `guidance_scale` from cfg, but NOT the
acceleration state. Add the accel type + resolved values so toggling acceleration
invalidates stale cached PNGs:

```python
accel = cfg.get("acceleration") or {}
accel_id = (accel.get("type") or "none").lower()
raw = (f"{prompt}|steps={steps}|w={width}|h={height}"
       f"|gs={guidance_scale}|neg={neg_prompt}|lora={lora_state}|model={model_id}"
       f"|accel={accel_id}")
```
> Note: `_prompt_cache_key` receives `cfg` already. The `steps`/`guidance_scale` it hashes
> are the *config* values, not the per-frame resolved ones. For correctness, when accel is
> active, hash the accel steps/guidance too (read them from `cfg['acceleration']`). Keep it
> simple: appending `accel={type}|asteps={..}|ags={..}` is enough.

---

## 3. Config changes

`config/config.yaml` — extend the existing slot (keep default OFF):

```yaml
  acceleration:
    type: "none"          # none | dmd2 | hyper_sd | lcm
    lora_path: ""         # path to the distillation LoRA .safetensors
    lora_scale: 1.0       # fusion strength
    steps: 6              # 4–8 recommended for distilled models
    guidance_scale: 1.5   # distilled models need ~1–2 (NOT 6)
```

If there is a schema model for `image_gen` in `config/config_schema.py`, add the
`lora_scale` field and lower the documented default guidance. If `image_gen` uses
`extra="allow"`, the YAML keys pass through — still update the comment.

---

## 4. How to obtain a DMD2 LoRA (for the operator / manual test)

DMD2 for SD 1.5 is distributed as a LoRA `.safetensors`. The operator downloads it once
(HuggingFace: `tianweiy/DMD2`, the SD1.5 LoRA variant) and sets:
```yaml
  acceleration:
    type: "dmd2"
    lora_path: "C:/models/dmd2_sd15_lora.safetensors"
    steps: 4
    guidance_scale: 1.0
```
DMD2 is the first candidate because it keeps prompt adherence best at 4 steps. LCM and
Hyper-SD are alternatives (LCM also flips the scheduler — already handled).

Do NOT auto-download in code. The file is large and the path is operator-provided.

---

## 5. Tests (what CAN and CAN'T be tested)

CAN be unit-tested (mock the pipeline — no GPU):
- **`test_accel_off_uses_config_guidance`** — with `type: none`, the resolver yields the
  config guidance (6.0) and config steps (12).
- **`test_accel_on_overrides_steps_and_guidance`** — with `type: dmd2, steps: 4,
  guidance_scale: 1.0`, the resolver yields 4 and 1.0.
- **`test_cache_key_changes_with_accel`** — `_prompt_cache_key(same prompt, accel=none)` !=
  `_prompt_cache_key(same prompt, accel=dmd2)`.
- **`test_accel_missing_lora_path_warns_not_crashes`** — `type: dmd2`, `lora_path` missing
  → no exception (mock `load_lora_weights`); falls through to step/guidance overrides.

To make the resolver testable, consider extracting it into a tiny pure helper:
```python
def _resolve_steps_guidance(cfg: dict) -> tuple[int, float]:
    accel = cfg.get("acceleration") or {}
    if (accel.get("type") or "none").lower() != "none":
        return int(accel.get("steps", 6)), float(accel.get("guidance_scale", 1.5))
    return int(cfg.get("steps", 12)), float(cfg.get("guidance_scale", 6.0))
```
Then call it from `_stable_diffusion` and test the helper directly.

CANNOT be unit-tested (needs GPU + real LoRA) — must be a MANUAL on-hardware check:
- Fused accel LoRA actually speeds up generation and still produces coherent images.
- Character face-lock LoRAs still load on top of the fused accel weights without error.

Add the unit tests to `tests/test_image_accel.py`. Mock `diffusers`/torch so no model
loads (follow the mocking pattern in existing `tests/`).

---

## 6. Verification checklist

```powershell
# 1. Diagnostics clean
#    getDiagnostics on: video/image_gen/image_gen.py, config/config_schema.py, config/config.yaml

# 2. File parses + imports
venv\Scripts\python.exe -c "import ast; ast.parse(open('video/image_gen/image_gen.py',encoding='utf-8').read()); print('PARSE_OK')"

# 3. No leftover normal-guidance calls inside the inference loop
#    grep_search query: guidance_scale=cfg.get   → should appear ONLY in the resolver + cache key, NOT in tier1/2/3 calls

# 4. New unit tests pass
venv\Scripts\python.exe -m pytest tests/test_image_accel.py -q

# 5. Full suite still green
venv\Scripts\python.exe -m pytest tests/ -q

# 6. Default-off regression: with type:"none", a dry-run behaves exactly as before
echo "" | venv\Scripts\python.exe bootstrap_pipeline.py --topic "Accel Smoke" --duration 1 --segment-count 1 --no-resume --dry-run
```

MANUAL (operator, with a real DMD2 LoRA + GPU), document results in the PR:
- Set `type: dmd2`, run 1 segment, confirm images are coherent and generation is faster.
- Confirm character consistency (face-lock LoRA) still works alongside acceleration.

---

## 7. Scope guardrails

- **Default MUST stay `type: "none"`.** No behavior change for existing runs.
- Do NOT auto-download any model/LoRA in code.
- Keep the whole acceleration block inside `try/except` with graceful fallback — a bad
  LoRA path or unsupported `fuse_lora` must NOT crash the run (log + continue at normal
  speed).
- Do NOT change `_stable_diffusion`'s signature or the per-frame face-lock logic beyond
  what GAP A requires.
- After done, tick `10.7` in `.kiro/specs/production-quality-fixes/tasks.md`.

## 8. Definition of done

- Accel LoRA fuses and survives per-frame adapter swaps (GAP A closed).
- Resolved guidance scale used at all 4 inference call sites (GAP B closed).
- Cache key includes accel state.
- Config has `lora_scale` + corrected guidance default; default still OFF.
- Unit tests (resolver + cache key + missing-path) pass; full suite green; module imports.
- Manual GPU check documented.
- Task 10.7 ticked in tasks.md.
