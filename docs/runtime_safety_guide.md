# Runtime Safety Guide

To guarantee stable local execution on consumer hardware with **6GB VRAM** limits, the system enforces strict resource management constraints.

---

## 1. VRAM & GPU Model Eviction

Bonsai 4B (image generation, ~3.5GB peak), Supertonic 3 / OmniVoice (voice rendering — Supertonic is CPU-only so does not need eviction), and local LLMs (Ollama) cannot reside in VRAM simultaneously without causing Out-of-Memory (OOM) failures.

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

## 4. Bonsai Image OOM Recovery Ladder (D1, 2026-06-04)

When Bonsai 4B runs out of VRAM during image generation,
`video/image_gen/image_gen.py` implements a 2-tier automatic recovery
(reduced from SD's 3-tier — Bonsai is sequential-VRAM only and does
not benefit from CPU offload on a 6GB card):

| Tier | Action |
|---|---|
| 1 (default) | Normal Bonsai call, `steps=4` (configurable) |
| 2 (fallback) | Retry with `max(2, steps * 0.5)` steps |
| skip + log | Both tiers OOM → record event in `oom_report.json`, emit placeholder frame, continue with next frame |

OOM reports are written to `studio_outputs/*/oom_report.json`
(accessible via `image_gen.get_oom_report()`) and the frame cache key
is invalidated so retry-on-resume can regenerate.

**Note:** The previous 3-tier SD ladder (reduce steps → CPU offload →
512×512) is **deprecated**. Bonsai's gemlite kernel is already VRAM-
optimized for 4-bit ternary inference and the 3rd tier was never
reached in practice. Sequential VRAM (no `enable_model_cpu_offload()`)
is enforced because peak is only ~3.5GB on RTX 4050 6GB.

---

## 5. Python Environment Safety (2026-06-08)

### Venv guard

`bootstrap_pipeline.py` now enforces running inside the project virtual
environment. If invoked with system Python (e.g. `python bootstrap_pipeline.py`
instead of `venv\Scripts\python.exe bootstrap_pipeline.py`), it checks
`sys.prefix != sys.base_prefix` and exits with:

```
ERROR: This pipeline must run inside the project virtual environment.
Use: venv\Scripts\python.exe bootstrap_pipeline.py
```

This prevents cryptic `ModuleNotFoundError` crashes from missing
dependencies (system Python is 3.14.5; the venv is 3.12.13).

### Pyarrow stub (Windows atexit crash)

On Windows, importing `pyarrow` triggers native DLL loading
(`arrow_python.dll`, etc.). At process exit, CPython's module cleanup
can unload these DLLs in an order that causes an access violation
(0xC0000005) — a hard crash that masks the test exit code.

**Fix (in `tests/conftest.py`):**
1. `os.environ["PYARROW_IGNORE_CPP_SHUTDOWN"] = "1"` — tells pyarrow to
   skip C++ shutdown on exit
2. A module-level stub replaces `pyarrow` in `sys.modules` before any
   real import can occur
3. `cleanup_numbered_dir` monkeypatch wraps `os.rmdir` in
   `contextlib.suppress(PermissionError)` — suppresses the benign
   `PermissionError` from `pytest-current` symlink cleanup

**If a test needs real pyarrow:** `del sys.modules["pyarrow"]` and
import the real module inside that test only.

## 6. TTS Worker Subprocess Safety (2026-06-04)

All TTS engines (Supertonic 3, OmniVoice, F5, etc.) use a **persistent
`--serve` worker subprocess** pattern — the parent process spawns the
worker once, then sends JSON-over-stdin requests and receives binary
WAV on stdout. This avoids the ~3s ONNX model load on every TTS call.

### Encoding (P6-2)

On Windows, the worker subprocess inherits `sys.stdout = cp1252` from
PowerShell. When the worker tries to `print("Devanagari text")` for
debug logging, it crashes with `UnicodeDecodeError: 'charmap' codec`.

**Fix (already applied):** all worker spawns in
`audio/supertonic_worker.py:spawn()` and
`audio/omnivoice_worker.py:spawn()` pass
`env={**os.environ, "PYTHONIOENCODING": "utf-8"}` in `Popen`. This
forces the child to use UTF-8 stdout, regardless of the parent's code
page.

If you write a new worker, copy this env injection. It is **not**
optional on Windows.

### Danda chunker bug (P6-1)

Supertonic 3's vendored `supertonic/utils.py:39` chunker regex
(`r"(?<=[.!?])\s+"`) only splits on Latin punctuation, not on Devanagari
danda `।`. Multi-sentence Hindi text collapses into a single chunk >
ONNX attention limit, crashing with `Mul_13 broadcast error`.

**Fix (already applied):** `audio/supertonic_worker.py:92` does
`text = text.replace("।", ". ")` before passing to `tts.synthesize()`.

**Defense in depth:** `tts.supertonic.max_chunk_length: 150` in
`config/config.yaml` forces chunking to ~150 chars per chunk
independently of sentence boundaries. If a chunker bug recurs (e.g.
exclamation `!` in another language), this catches it.

### Worker lifecycle

- The persistent worker is started lazily on the first `tts_generate()` call.
- It is killed on `shutdown_supertonic_worker()` (called from
  `utils/shutdown.py` cleanup chain).
- If a worker crashes mid-request, `_SupertonicWorker._send()` raises
  and the parent's `tts_generate()` falls back to the next engine in
  the chain (`omnivoice` → `edge-tts`).
- **Don't** call `tts_generate()` from multiple threads simultaneously
  with the same engine — the worker is single-threaded. Use
  `global_scheduler.task("light", ...)` to serialize.

### Voice JSON safety

Voice style JSONs in `character_voices/` are loaded read-only. The DIY
extractor writes new ones via temp-file + atomic replace. If a JSON is
malformed, Supertonic 3 raises a JSON parse error in the worker, which
the parent catches and falls back to omnivoice.

### CPU vs VRAM

| Engine | Subprocess | VRAM | CPU |
|---|---|---|---|
| Supertonic 3 | Yes | 0 | High (4 threads) |
| OmniVoice | Yes | ~2 GB | Low |
| Edge TTS | No (network) | 0 | 0 |
| F5 | Yes | ~1.5 GB | Medium |

Since Supertonic 3 is **CPU-only**, it does **NOT** need to participate
in the Ollama eviction dance. It can run concurrently with Stable
Diffusion (SD takes the GPU, Supertonic 3 takes the CPU). This is the
key reason it became the default.

### Persistent-worker memory leak check

Run a 1-hour TTS burn-in test before declaring production-ready:
```powershell
venv\Scripts\python.exe external\long_burnin_test.py  # 1hr synth test
# Check: worker RSS should stabilize after ~5 min, no monotonic growth
```
