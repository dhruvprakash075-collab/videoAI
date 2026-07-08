# Runtime Safety Guide

To guarantee stable local execution on consumer hardware with **6GB VRAM** limits, the system enforces strict resource management constraints.

---

## 1. VRAM & GPU Model Eviction

ComfyUI / the configured image checkpoint (currently `meinamix_meinaV11.safetensors`), IndicF5 / Supertonic / OmniVoice (voice rendering), and local LLMs (Ollama) compete for local CPU/GPU memory. Supertonic is CPU-only, but GPU-backed engines and ComfyUI must be serialized to avoid Out-of-Memory failures.

### Two-Stage Eviction Process (`core/runtime/ollama.py`)

Stage 1 — **Soft Evict**: Calls Ollama's `/api/generate` with `keep_alive=0` to force-unload the model.

Stage 2 — **Hard Evict**: If VRAM remains low after Stage 1, the system calls `/api/ps` to enumerate all loaded models and evicts each one individually.

### VRAM Polling Loop (A1 Feature)
After eviction, the system polls `torch.cuda.mem_get_info()` in a loop, waiting up to `performance.vram_evict_wait_s` (default **15 seconds**) for free VRAM to reach the `performance.vram_sd_threshold_gb` threshold (default **4.5 GB**) before loading the next heavy model. This prevents race conditions where SD tries to load while Ollama is still freeing VRAM.

---

## 2. Concurrency Locks & Task Scheduling (`utils/concurrency.py`)

| Slot Type | Limit | Timeout | Used For |
|---|---|---|---|
| `"heavy"` | **1 active** | **1800s** (30 min) | ComfyUI image gen, GPU-backed TTS |
| `"light"` | **16 active** | **60s** | Video rendering, assembler |

* **GPU Task Slots**: `global_scheduler.task("heavy", ...)` restricts concurrent heavy GPU processes to **1**. A task that hangs past 1800s surfaces a `TimeoutError`.
* **Agent Lock**: `utils.concurrency.crewai_lock` is an **`RLock`** (not a plain Lock — P3-14 fix) that serializes all CrewAI `crew.kickoff()` executions to prevent litellm executor corruption from concurrent calls.

> **Rule**: Any ComfyUI image generation or heavy TTS must go through `global_scheduler.task("heavy", ...)`. Any render/assemble task uses `global_scheduler.task("light", ...)`.

---

## 3. Circuit Breaker System (`utils/circuit_breaker.py` core, `utils/crewai_breaker.py` + `utils/ollama_client.py` consumers)

The core implementation lives in `utils/circuit_breaker.py`:
- `CircuitBreaker` — per-instance 3-state machine (CLOSED/OPEN/HALF_OPEN)
- `CircuitBreakerRegistry` — global dict of named breakers, auto-creates on first access
- `BreakerOpen(name, cooldown_s)` — exception carrying the real remaining cooldown

### States
- **Closed** (normal): requests flow through to Ollama/CrewAI.
- **Open** (tripped): requests immediately raise `BreakerOpen` without waiting. `cooldown_s` is the **real remaining** cooldown (P5-1 fix — was hardcoded 0 before).
- **Half-Open**: after cooldown expires, the next request is tried. Success resets to Closed; failure re-opens.

### Key Rule: Shared Breaker State
`OllamaClient._breaker()` and `guarded_crewai_kickoff()` both resolve to the same `CircuitBreakerRegistry` — they share breaker state per model name. A model that fails via `OllamaClient.generate()` and via `crew.kickoff()` both open the same breaker, preventing double-loading attempts.

### Direct access pattern
```python
from utils.circuit_breaker import CircuitBreakerRegistry, BreakerOpen

cb = CircuitBreakerRegistry.get("ollama:zephyr-writer", fails=3, cooldown=30)
if cb.allow_request():
    try:
        result = call_service()
        cb.record_success()
    except Exception:
        cb.record_failure()
        raise
else:
    raise BreakerOpen("zephyr-writer", cb.cooldown_remaining_s())
```

### CrewAI wrapper pattern (preferred for LLM calls)
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

## 4. ComfyUI Image OOM Protection

Image generation uses **ComfyUI** (currently the manga identity/pose workflow
with `meinamix_meinaV11.safetensors`).
VRAM protection is handled by the heavy-task scheduler
(`global_scheduler.task("heavy", ...)` ensures only one GPU task runs
at a time) and the VRAM polling loop (Section 1). Preflight checks
VRAM before starting the image phase; if free VRAM is below the
`vram_sd_threshold_gb` (4.5 GB), SD loading is gated until eviction
completes.

ComfyUI itself is auto-started by the pipeline when needed (config:
`image_gen.comfyui.auto_start: true`) and unloaded after each image batch
to release VRAM for subsequent segments.

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

### Optional dependency stubs (CI / test env)

`tests/conftest.py:_install_optional_dependency_stubs()` (called from `tests/conftest.py:_setup_stubs()`) injects
lightweight `types.ModuleType` stubs for heavy or native-DLL packages
so tests using `patch(...)` never load the real modules:

| Package | Reason for stub |
|---------|----------------|
| `torch` (+ `torch.cuda`) | Avoid 200MB download on CI. Tests mock `torch.cuda.*` — no real GPU calls. |
| `pyarrow` | Avoids Windows native-DLL shutdown crash (access violation at exit). |
| `crewai` | Avoids pulling in litellm / 100+ transitive deps. |
| `faster_whisper` | Not installed on CI; tests only need it importable for `patch()`. |
| `whisper` | Same as `faster_whisper`. |

**Mechanism:** Each stub is a plain `types.ModuleType` with the minimal
attributes needed for `unittest.mock.patch` to resolve the dotted path.
When a test does `patch("torch.cuda.is_available")`, `patch` finds the
stub in `sys.modules` and swaps the target attribute for a mock. The
tested code's lazy `import torch` inside the function body gets the
same stub — no real CUDA initialisation, no GPU memory allocated.

**If a test needs a real module:** `del sys.modules["m"]` before
importing it inside that test only.

**CI install strategy:** Lightweight test deps are installed via
`pip install` in `.github/workflows/ci.yml`:
`pytest`, `pytest-mock`, `pytest-cov`, `pydantic`, `pyyaml`, `httpx`,
`tqdm`, `langgraph`, `requests`, `beautifulsoup4`, `fastapi`,
`python-multipart`, `pydub`, `soundfile`, `psutil`, `playwright`.
Torch (200MB+) is never downloaded — the stub covers all test needs.

### Config and fallback error handling

Runtime config loaders treat YAML roots as mappings. If `config.yaml` or a
project YAML file parses to a list/string instead of a dict, `load_config()`
now fails clearly with `TypeError` instead of crashing later with a hidden
`.get()` / `.items()` error. Smaller config readers such as the worker
ComfyUI URL lookup, vision cache hashing, prompt loading, and style resolver
fall back to `{}` when a non-mapping YAML root is safe to ignore.

Broad runtime fallbacks should be observable. Do not add silent
`except Exception: pass` blocks in pipeline code. If the fallback is
intentional, record the reason with `log.debug(...)`, `log.warning(...)`, or
an existing degradation ledger entry.

## 6. TTS Worker Subprocess Safety (2026-06-04)

Local TTS engines use subprocess workers. Supertonic and OmniVoice support
persistent `--serve` workers; IndicF5 is invoked through its configured Python
environment and reference audio/text settings in `tts.indicf5`.

### Encoding (P6-2)

On Windows, the worker subprocess inherits `sys.stdout = cp1252` from
PowerShell. When the worker tries to `print("Devanagari text")` for
debug logging, it crashes with `UnicodeDecodeError: 'charmap' codec`.

**Fix (already applied):** worker spawns in
`audio/audio_proxy.py`, `audio/supertonic_worker.py`, and
`audio/omnivoice_worker.py` pass
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
  the chain (`supertonic` → `omnivoice`).
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

Since Supertonic 3 is CPU-only, it does not need the Ollama eviction dance.
IndicF5 and OmniVoice should be treated according to their configured runtime
and hardware usage.

### Persistent-worker memory leak check

Run a 1-hour TTS burn-in test before declaring production-ready:
```powershell
venv\Scripts\python.exe external\long_burnin_test.py  # 1hr synth test
# Check: worker RSS should stabilize after ~5 min, no monotonic growth
```
