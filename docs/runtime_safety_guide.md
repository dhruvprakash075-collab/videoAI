# Runtime Safety Guide

To guarantee stable local execution on consumer hardware with **6GB VRAM** limits, the system enforces strict resource management constraints.

---

## 1. VRAM & GPU Model Eviction

Stable Diffusion (image generation), F5-TTS/OmniVoice (voice rendering), and local LLMs (Ollama) cannot reside in VRAM simultaneously without causing Out-of-Memory (OOM) failures.

### Two-Stage Eviction Process (`core/segment_runner.py`)

Stage 1 — **Soft Evict**: Calls Ollama's `/api/generate` with `keep_alive=0` to force-unload the model.

Stage 2 — **Hard Evict**: If VRAM remains low after Stage 1, the system calls `/api/ps` to enumerate all loaded models and evicts each one individually.

### VRAM Polling Loop (A1 Feature)
After eviction, the system polls `torch.cuda.mem_get_info()` in a loop, waiting up to `performance.vram_evict_wait_s` (default **15 seconds**) for free VRAM to reach the `performance.vram_sd_threshold_gb` threshold (default **4.5 GB**) before loading the next heavy model. This prevents race conditions where SD tries to load while Ollama is still freeing VRAM.

---

## 2. Concurrency Locks & Task Scheduling (`utils/concurrency.py`)

| Slot Type | Limit | Timeout | Used For |
|---|---|---|---|
| `"heavy"` | **1 active** | **1800s** (30 min) | SD image gen, OmniVoice TTS |
| `"light"` | **16 active** | **60s** | Video rendering, assembler |

* **GPU Task Slots**: `global_scheduler.task("heavy", ...)` restricts concurrent heavy GPU processes to **1**. A task that hangs past 1800s surfaces a `TimeoutError`.
* **Agent Lock**: `utils.concurrency.crewai_lock` is an **`RLock`** (not a plain Lock — P3-14 fix) that serializes all CrewAI `crew.kickoff()` executions to prevent litellm executor corruption from concurrent calls.

> **Rule**: Any SD image generation or heavy TTS must go through `global_scheduler.task("heavy", ...)`. Any render/assemble task uses `global_scheduler.task("light", ...)`.

---

## 3. Circuit Breaker System (`utils/crewai_breaker.py` + `utils/ollama_client.py`)

The codebase guards against hung LLMs via a **shared per-model circuit breaker** state machine:

### States
- **Closed** (normal): requests flow through to Ollama/CrewAI.
- **Open** (tripped): requests immediately raise `BreakerOpen(model, cooldown_s)` without waiting. `cooldown_s` is the **real remaining** cooldown (P5-1 fix — was hardcoded 0 before).
- **Half-Open**: after cooldown expires, the next request is tried. Success resets to Closed; failure re-opens.

### Key Rule: Shared Breaker State
`OllamaClient._breaker()` and `guarded_crewai_kickoff()` **share the same breaker state** per model. A model that fails via `OllamaClient.generate()` and via `crew.kickoff()` both open the same breaker — preventing double-loading attempts.

### Usage Pattern
```python
from utils.crewai_breaker import guarded_crewai_kickoff, BreakerOpen

try:
    result = guarded_crewai_kickoff(crew, model_name="my-model", timeout_s=240)
except BreakerOpen as e:
    # e.cooldown_s is the REAL remaining cooldown
    log.warning(f"Breaker open for {e.cooldown_s:.1f}s — falling back")
```

> **Warning**: Never call `crew.kickoff()` directly. Always use `guarded_crewai_kickoff()` which enforces the 240s `timeout_s` AND the circuit breaker.

---

## 4. Stable Diffusion OOM Recovery Ladder (D1)

When SD runs out of VRAM during image generation, `video/image_gen/image_gen.py` implements a 3-tier automatic recovery:

| Tier | Action |
|---|---|
| 1 | Reduce steps and CFG scale, retry |
| 2 | Move pipe to CPU, generate, move back |
| 3 | Emergency 512×512 fallback resolution |

OOM reports are written to `studio_outputs/*/oom_report.json` (accessible via `image_gen.get_oom_report()`).
