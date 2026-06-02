# Implementation Brief — Phase 0 + Phase 0.5 (TTS Timestamp Fix)

> **Audience:** Another AI assistant (any model) with no prior context. Pick this
> up, follow it step by step, and produce a green test suite at the end.
>
> **Goal of this brief:** Two small phases, ~1 working day total. Cleans up
> technical debt left by previous work (Phase 0) and fixes a real bug where the
> TTS workers never return word timestamps, causing the renderer to run Whisper
> as a fallback for every single segment (Phase 0.5).

---

## 0. Project Context (read first)

**Project:** Video.AI — a local-only video generation pipeline. Topic → story
plan → Hindi voiceover (OmniVoice TTS) → Stable Diffusion images with LoRA
face-lock → Ken Burns MP4 with Devanagari subtitles. All local on a Windows 11
RTX 4050 (6 GB VRAM) + 16 GB RAM box. Python 3.12.13 in `venv/`.

**Working directory:** `C:\Video.AI`
**Python:** `venv\Scripts\python.exe` (always use this; never `python` directly)
**OS:** Windows 11, PowerShell 7+
**Git:** Repo with **0 commits** — all changes go in the working tree, no
branches, no history. Just modify files in place.

**The two plans you're implementing from:**
- `C:\Users\dhruv\OneDrive\Documents\implementation_plan_v6.md` — overall plan
- `C:\Users\dhruv\OneDrive\Documents\pipeline_diagram_v6.md` — visual

**Read these two files first** (5 min). Then come back here.

---

## 1. Critical Rules (DO NOT BREAK)

These come from `AGENTS.md` at the repo root. **Read that file too.**

1. **Run through `bootstrap_pipeline.py`**, never `python -m core.pipeline_long`
   directly. Bootstrap applies compat patches, runs preflight, handles shutdown.
2. **Only ONE model in VRAM at a time.** Ollama models must be force-evicted
   (`keep_alive=0`) before any GPU task. Use `evict_ollama_models(config, reason)`.
3. **Serialize ALL CrewAI `kickoff()`** through `utils.concurrency.crewai_lock`
   (an RLock).
4. **Use `global_scheduler.task("heavy", ...)`** for any GPU work (SD, TTS).
5. **All config changes go in `config/config.yaml`**, not in Python. Add a
   matching Pydantic field in `config/config_schemas.py` (note: **plural**, with
   the `s`).
6. **All paths are `pathlib.Path`**, no POSIX assumptions.
7. **Atomic writes only** (temp + replace) for any persisted JSON.
8. **`tests/conftest.py` autouse-resets `UIState`** between tests. If you add a
   new `UIState` class attribute, you MUST add it to `conftest.py`.
9. **DO NOT ADD ANY COMMENTS** to code unless explicitly asked. The project
   style is comment-free Python.
10. **Verify before declaring done:** `pytest tests/ -q` must show **290+ tests
    passing, 0 failing** (12 deprecation warnings from `crewai` are expected).
    `ruff check .` must show **0 errors**.

---

## 2. Verification Baseline (BEFORE you start)

Run these and capture the output. You need the "before" numbers to prove you
didn't regress anything.

```powershell
cd C:\Video.AI
venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | Select-Object -Last 5
# Expected: "290 passed, 12 warnings in ~50s"

venv\Scripts\python.exe -m ruff check . 2>&1 | Select-Object -Last 3
# Expected: "All checks passed!"
```

Save the "before" output. You'll compare against it at the end.

---

## 3. Phase 0 Tasks (hygiene — 8 fixes)

Work through these **in order**. Each is small.

### Task 0.1 — Pin missing dependencies in `requirements.txt`

**File:** `C:\Video.AI\requirements.txt`
**Current state:** `langgraph` and `playwright` are installed in `venv\` but
not pinned in this file. `feedparser` (for Phase 3) is also missing.
**Why:** Cloning the repo on a new machine would `ImportError` on first run.

**Action:** Open the file, find the last line, append:

```
langgraph>=0.2
playwright>=1.40
pytest-playwright>=0.5
feedparser>=6.0
```

(Use the actual versions installed: `venv\Scripts\python.exe -m pip show
langgraph playwright pytest-playwright feedparser` to get exact versions, then
pin them as `==X.Y.Z`.)

**Verify:** `venv\Scripts\python.exe -m pip install -r requirements.txt` should
succeed without installing anything new (everything already satisfied).

### Task 0.2 — Add missing config sections to `config/config.yaml`

**File:** `C:\Video.AI\config\config.yaml`
**Current state:** The `tts:`, `script:`, `performance:`, and `upload:`
sections exist. The `critic:`, `research:`, `seo:`, and `source:` sections do
not.

**Action:** Find the `upload:` section at the **end** of the file (around
line 247). **After** it, add these 4 sections:

```yaml
# ── Critic (self-critique quality gate) ─────────────────────────
critic:
  enabled: true
  threshold: 60              # minimum score 0-100 to pass
  max_rewrites: 2            # max rewrite attempts before giving up

# ── Research (auto-topic discovery, Phase 3) ────────────────────
research:
  enabled: true
  sources: ["wikipedia", "rss"]
  rss_urls: []
  user_agent: "VideoAI/6.0 (+https://github.com/...)"

# ── SEO (YouTube metadata generation) ────────────────────────────
seo:
  enabled: true
  title_max_chars: 100
  description_max_chars: 5000
  tags_count: 15

# ── Source (dual-entry ingestion, Phase 1) ───────────────────────
source:
  allowed_extensions: [".txt", ".md", ".pdf", ".docx"]
  max_words: 50000           # soft cap — warn but proceed if over
  url_timeout_s: 30
  user_agent: "VideoAI/6.0 (+https://github.com/...)"
```

Also add **two keys** to the existing `script:` section (find it around
line 156):

```yaml
script:
  words_per_segment: 100     # ← may already exist
  # ... existing keys ...
  critic_enabled: true
  critic_threshold: 60
  critic_max_rewrites: 2
```

**Why these `script.*` keys:** The hybrid node in `core/pipeline_graph.py:83`
reads `critic_max_rewrites` via `self.ctx.config.get("script", {}).get(...)`.
The default fallback is `2`, but with the config key absent, you can never
tune it. Same for threshold.

### Task 0.3 — Add matching Pydantic fields in `config/config_schemas.py`

**File:** `C:\Video.AI\config\config_schemas.py` (note the **plural**)

**Action:** Read the file first to understand the existing Pydantic models
(there are already models for `TTSConfig`, `PerformanceConfig`, etc.). Then
add 4 new models and 1 new field on `TTSConfig`:

```python
class CriticConfig(BaseModel):
    enabled: bool = True
    threshold: int = 60
    max_rewrites: int = 2

class ResearchConfig(BaseModel):
    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: ["wikipedia", "rss"])
    rss_urls: list[str] = Field(default_factory=list)
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"

class SEOConfig(BaseModel):
    enabled: bool = True
    title_max_chars: int = 100
    description_max_chars: int = 5000
    tags_count: int = 15

class SourceConfig(BaseModel):
    allowed_extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".pdf", ".docx"])
    max_words: int = 50000
    url_timeout_s: int = 30
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"

class AlignmentConfig(BaseModel):
    enabled: bool = True
    model: str = "base"        # tiny | base | small | vasista22/whisper-hindi-small
    device: str = "cpu"
    compute_type: str = "int8"
```

Then **add** these fields to the top-level config model (look for the existing
`class VideoAIConfig(BaseModel)` or similar):

```python
    critic: CriticConfig = Field(default_factory=CriticConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    seo: SEOConfig = Field(default_factory=SEOConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
```

And add to the existing `TTSConfig`:

```python
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
```

**Verify with:** `venv\Scripts\python.exe -c "from config.config_schemas import VideoAIConfig; c = VideoAIConfig(); print(c.critic.threshold, c.source.max_words, c.tts.alignment.model)"` — should print `60 50000 base`.

### Task 0.4 — Fix bare `except:` in `utils/youtube_uploader.py`

**File:** `C:\Video.AI\utils\youtube_uploader.py`
**Line 106-107** currently has:
```python
            try:
                page.click("ytcp-button#toggle-button", timeout=5000)
            except:
                pass # Might already be expanded
```

**Fix:** Replace with:
```python
            try:
                page.click("ytcp-button#toggle-button", timeout=5000)
            except PlaywrightTimeoutError:
                pass
```

The `PlaywrightTimeoutError` is already imported at line 8.

### Task 0.5 — Remove unused `import os` in `utils/youtube_uploader.py`

**File:** `C:\Video.AI\utils\youtube_uploader.py`
**Line 4:** `import os` — never used in the file.

**Fix:** Delete that line.

### Task 0.6 — Remove `script-reviewer` from `config/config.yaml`

**File:** `C:\Video.AI\config\config.yaml`
**Line 18** currently has:
```yaml
  reviewer: "script-reviewer"      # Fast script review. NOT created — degrades gracefully (auto-approve). Create/pull to enable.
```

**Why remove:** This model is never created in Ollama, so the critic
auto-approves everything. Per the v6.1 decision, the writer self-critiques
via prompt swap, so we don't need a separate reviewer model.

**Fix:** Delete line 18 (and any trailing line that becomes orphaned). Keep the
`director:`, `writer:`, `writer_scratch:`, `writer_adapt:` lines.

### Task 0.7 — Remove reviewer call in `core/segment_runner.py`

**File:** `C:\Video.AI\core\segment_runner.py`
**Lines 491-493** (inside `critic_node`) currently have:
```python
        log.debug(f"  Seg {i}: Reviewing script with script-reviewer...")
        from utils.specialized_models import review_script_fast
        review_result = review_script_fast(script, plan, context, config.get("characters", {}))
```

**Fix:** Replace these 3 lines with a stub that auto-approves (this node will
be properly replaced in Phase 4, but for now we just stop calling the dead
reviewer):
```python
        log.debug(f"  Seg {i}: Critic node — using legacy auto-approve (Phase 4 will replace)")
        review_result = {"approved": True, "review_unavailable": True}
```

And in the same node, **after** the existing `if review_result.get("review_unavailable"):` block, the code already returns `critic_approved=True`. So this stub is the minimum-change fix.

### Task 0.8 — Add tests for the existing LangGraph skeleton

**File:** `C:\Video.AI\tests\test_pipeline_graph.py` (NEW FILE)

**Why:** The previous AI built `core/pipeline_graph.py` (120 LoC) but added
zero tests. We need regression coverage.

**Action:** Create the file with these 6 tests:

```python
"""test_pipeline_graph.py - Regression tests for the LangGraph skeleton in
core/pipeline_graph.py. Verifies the graph builds, the routing logic is
correct, and state propagates through nodes.
"""
import pytest
from core.pipeline_graph import SegmentGraphBuilder, SegmentState, END


class _FakeCtx:
    """Minimal context for SegmentGraphBuilder — supplies config + node fns."""
    def __init__(self, max_rewrites=2):
        self.config = {
            "script": {"critic_max_rewrites": max_rewrites, "critic_threshold": 60},
        }
    def do_write_script(self, state): return {"script": "draft"}
    def do_critic(self, state):       return {"critic_approved": True, "critic_feedback": "", "rewrites_attempted": 1}
    def do_translate(self, state):    return {"devanagari_script": "ट्रांसलेट"}
    def do_tts(self, state):          return {"audio_path": "/tmp/a.wav"}
    def do_image_gen(self, state):    return {"images": ["/tmp/i.png"]}
    def do_render(self, state):       return {"mp4_path": "/tmp/v.mp4"}


def test_graph_builds_without_error():
    builder = SegmentGraphBuilder(_FakeCtx())
    graph = builder.build()
    assert graph is not None


def test_route_after_critic_aborted_returns_end():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": True, "critic_approved": True}
    assert builder.route_after_critic(state) == END


def test_route_after_critic_approved_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"aborted": False, "critic_approved": True, "rewrites_attempted": 0}
    assert builder.route_after_critic(state) == "translate_node"


def test_route_after_critic_rejected_under_max_returns_writer():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 1, "i": 1}
    assert builder.route_after_critic(state) == "write_script_node"


def test_route_after_critic_rejected_at_max_returns_translate():
    builder = SegmentGraphBuilder(_FakeCtx(max_rewrites=2))
    state = {"aborted": False, "critic_approved": False, "rewrites_attempted": 2, "i": 1}
    # Forgiving escape hatch: never loop forever
    assert builder.route_after_critic(state) == "translate_node"


def test_state_script_propagates_across_write_to_critic():
    """Regression: AGENTS.md 'atomic state' rule — script must survive the
    round-trip from write_script_node to critic_node."""
    builder = SegmentGraphBuilder(_FakeCtx())
    state = {"i": 1, "plan": {}, "context": ""}
    write_out = builder.write_script_node(state)
    assert "script" in write_out
    new_state = {**state, **write_out}
    assert new_state["script"] == "draft"
    # Critic can read it
    critic_out = builder.critic_node(new_state)
    assert "critic_approved" in critic_out
```

**Verify:** `venv\Scripts\python.exe -m pytest tests/test_pipeline_graph.py -v`
should show 6 passed.

---

## 4. Phase 0.5 Tasks (TTS Timestamp Fix — 5 changes)

**The bug:** `audio/omnivoice_worker.py:285` and `audio/f5_worker.py:322`
return `{"status": "success", "wav_path": wav}` only — no `word_timestamps`
key. Downstream, `audio/audio_proxy.py:898` reads `result.get("word_timestamps")`
→ `None` → `video/renderer/assembler.py:585` runs Whisper as a fallback for
**every** segment.

**The fix:** Move the Whisper alignment call from the renderer to the TTS worker.

### Task 0.5.1 — Create `audio/tts_alignment.py` (new file)

**File:** `C:\Video.AI\audio\tts_alignment.py` (NEW FILE)

```python
"""tts_alignment.py - Generate word-level timestamps for TTS output.

Wraps faster-whisper (CPU int8, no VRAM impact) to produce per-word timing
JSON. Called from the TTS worker after audio synthesis, so the renderer
always has real audio timing and never falls back to running Whisper itself.
"""
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level cache so multiple TTS calls in one run share the same model.
_alignment_model = None
_alignment_lock = threading.Lock()
_alignment_model_name = None


def _get_alignment_model(model_name: str, device: str, compute_type: str):
    global _alignment_model, _alignment_model_name
    if _alignment_model is not None and _alignment_model_name == model_name:
        return _alignment_model
    with _alignment_lock:
        if _alignment_model is None or _alignment_model_name != model_name:
            from faster_whisper import WhisperModel
            _alignment_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _alignment_model_name = model_name
            log.info(f"tts_alignment: loaded faster-whisper {model_name} ({device}, {compute_type})")
    return _alignment_model


def align_audio(wav_path: Path, model_name: str = "base",
                device: str = "cpu", compute_type: str = "int8") -> Path | None:
    """Run faster-whisper on a WAV file, write word timestamps to {wav}.words.json.

    Returns the JSON path on success, None on any failure (does not raise).
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        log.warning(f"tts_alignment: WAV not found: {wav_path}")
        return None
    json_path = wav_path.with_suffix(".words.json")
    try:
        model = _get_alignment_model(model_name, device, compute_type)
        segments_gen, _info = model.transcribe(
            str(wav_path), beam_size=1, word_timestamps=True, vad_filter=True
        )
        words = [
            {"word": (w.word or "").strip(), "start": w.start, "end": w.end}
            for seg in segments_gen
            for w in (seg.words or [])
            if (w.word or "").strip()
        ]
        json_path.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"tts_alignment: wrote {len(words)} words to {json_path.name}")
        return json_path
    except Exception as e:
        log.warning(f"tts_alignment: failed for {wav_path.name}: {e}")
        return None
```

### Task 0.5.2 — Wire alignment into `omnivoice_worker.py`

**File:** `C:\Video.AI\audio\omnivoice_worker.py`

**Find lines 285 and 304** (the two `print(json.dumps({"status": "success", "wav_path": wav}))` calls — one in persistent mode, one in one-shot mode).

**For each**, replace the line with this block. Read `config.yaml` once at module import to know whether alignment is enabled:

At the top of the file (after existing imports), add:
```python
def _maybe_align(wav_path: str) -> str | None:
    try:
        from config import load_config
        cfg = load_config()
        align = cfg.get("tts", {}).get("alignment", {})
        if not align.get("enabled", True):
            return None
        from audio.tts_alignment import align_audio
        result = align_audio(
            Path(wav_path),
            model_name=align.get("model", "base"),
            device=align.get("device", "cpu"),
            compute_type=align.get("compute_type", "int8"),
        )
        return str(result) if result else None
    except Exception as e:
        log.warning(f"_maybe_align failed: {e}")
        return None
```

And add `from pathlib import Path` to the imports at the top if not already there.

Then in **both** success print sites (lines 285 and 304), change:
```python
print(json.dumps({"status": "success", "wav_path": wav}))
```
to:
```python
word_timestamps = _maybe_align(wav)
print(json.dumps({"status": "success", "wav_path": wav, "word_timestamps": word_timestamps}))
```

### Task 0.5.3 — Wire alignment into `f5_worker.py`

**File:** `C:\Video.AI\audio\f5_worker.py`

Same treatment as OmniVoice: copy the `_maybe_align` helper, replace both
success print sites (lines 322 and 342).

### Task 0.5.4 — Add regression-detector warning in `assembler.py`

**File:** `C:\Video.AI\video\renderer/assembler.py`
**Line 590** currently has:
```python
        log.info(f"Generating word-level subtitles using Whisper ({format_style})...")
```

**Fix:** Change to:
```python
        log.warning(
            f"REGRESSION: Whisper fallback fired for seg (format={format_style}). "
            f"TTS worker should have provided word_timestamps JSON. "
            f"Check tts.alignment.enabled in config.yaml."
        )
        log.info(f"Generating word-level subtitles using Whisper ({format_style})...")
```

The WARNING level is critical — it's the regression detector. If Phase 0.5
works, this WARNING should **never** appear in the logs.

### Task 0.5.5 — Add tests for the alignment flow

**File:** `C:\Video.AI\tests\test_tts_alignment.py` (NEW FILE)

```python
"""test_tts_alignment.py - Tests for the TTS→alignment→renderer flow."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def test_align_audio_writes_json_next_to_wav(tmp_path):
    """align_audio() writes {wav}.words.json with the right structure."""
    from audio.tts_alignment import align_audio

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)  # not a real WAV, but exists

    fake_word = MagicMock(word="hello", start=0.0, end=0.5)
    fake_word.word = "hello"
    fake_word.start = 0.0
    fake_word.end = 0.5
    fake_seg = MagicMock(words=[fake_word])
    fake_seg.words = [fake_word]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([fake_seg]), MagicMock())

    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        result = align_audio(wav, model_name="base")

    assert result == wav.with_suffix(".words.json")
    assert result.exists()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data == [{"word": "hello", "start": 0.0, "end": 0.5}]


def test_align_audio_returns_none_if_wav_missing(tmp_path):
    from audio.tts_alignment import align_audio
    assert align_audio(tmp_path / "nope.wav") is None


def test_align_audio_returns_none_on_whisper_failure(tmp_path):
    from audio.tts_alignment import align_audio
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"x")

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = RuntimeError("boom")
    with patch("audio.tts_alignment._get_alignment_model", return_value=fake_model):
        assert align_audio(wav) is None


def test_alignment_disabled_skips_call(tmp_path, monkeypatch):
    """When tts.alignment.enabled is false, _maybe_align returns None without
    importing or calling faster-whisper."""
    from audio import omnivoice_worker

    fake_cfg = {"tts": {"alignment": {"enabled": False}}}
    monkeypatch.setattr("config.load_config", lambda: fake_cfg)

    called = {"align_audio": False}
    def _spy(*a, **kw):
        called["align_audio"] = True
        return None
    monkeypatch.setattr("audio.tts_alignment.align_audio", _spy)

    assert omnivoice_worker._maybe_align(str(tmp_path / "x.wav")) is None
    assert called["align_audio"] is False


def test_alignment_enabled_calls_align_audio(tmp_path, monkeypatch):
    from audio import omnivoice_worker
    fake_cfg = {"tts": {"alignment": {"enabled": True, "model": "base"}}}
    monkeypatch.setattr("config.load_config", lambda: fake_cfg)

    expected = tmp_path / "out.words.json"
    monkeypatch.setattr("audio.tts_alignment.align_audio", lambda *a, **kw: expected)

    result = omnivoice_worker._maybe_align(str(tmp_path / "x.wav"))
    assert result == str(expected)


def test_tts_worker_result_dict_includes_word_timestamps_key():
    """Regression: the worker's success JSON must include the word_timestamps
    key (value may be null), so audio_proxy.py:898 doesn't fall through."""
    import json as _json
    # Inspect the source string for the literal key — this guards against
    # accidental removal in future edits.
    src = Path("audio/omnivoice_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src, "omnivoice_worker.py must emit 'word_timestamps' key in success JSON"

    src2 = Path("audio/f5_worker.py").read_text(encoding="utf-8")
    assert '"word_timestamps"' in src2, "f5_worker.py must emit 'word_timestamps' key in success JSON"
```

**Also** update `C:\Video.AI\tests\test_audio_crossfade.py` to assert that
`word_timestamps_json` is populated. Read that file first to see its existing
patch structure, then add one assertion at the end of each test that currently
verifies audio:

```python
        assert "word_timestamps" in result  # or whatever the local var is
```

(Read the file to find the right place; don't guess at variable names.)

---

## 5. End-to-End Verification

After all tasks, run the full suite. **All of these must pass:**

```powershell
cd C:\Video.AI

# 1. All tests pass
venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | Select-Object -Last 5
# Expected: "296+ passed, 12 warnings in ~55s"  (290 baseline + 6 pipeline_graph + 5 tts_alignment = 301)

# 2. Lint clean
venv\Scripts\python.exe -m ruff check . 2>&1 | Select-Object -Last 3
# Expected: "All checks passed!"

# 3. New config keys are loadable
venv\Scripts\python.exe -c "from config.config_schemas import VideoAIConfig; c = VideoAIConfig(); print(c.critic.threshold, c.source.max_words, c.tts.alignment.model)"
# Expected: "60 50000 base"

# 4. TTS worker source has the word_timestamps key
Select-String -Path "C:\Video.AI\audio\omnivoice_worker.py", "C:\Video.AI\audio\f5_worker.py" -Pattern '"word_timestamps"'
# Expected: 4 lines (2 files × 2 sites each = persistent + oneshot)
```

---

## 6. Manual Smoke Test (if you have time)

If Ollama is running and a 6GB model is available, do a real dry-run:

```powershell
cd C:\Video.AI
venv\Scripts\python.exe bootstrap_pipeline.py --skip-preflight --dry-run --topic "Real Hero Test" --yes
# Look for: "tts_alignment: wrote N words" in the logs
# Look for: NO "REGRESSION: Whisper fallback fired" warnings
# Look for: "Using provided word timestamps JSON" in renderer logs
```

If dry-run can't actually run TTS (no model), the unit tests are sufficient.

---

## 7. Rollback (if something breaks)

All changes are file-level. To undo:

```powershell
cd C:\Video.AI
# Save your changes first
Copy-Item requirements.txt requirements.txt.bak
Copy-Item config\config.yaml config\config.yaml.bak
Copy-Item config\config_schemas.py config\config_schemas.py.bak
# ... etc for each file you touched

# To rollback a single file: edit it back manually
# There is no git history — everything is the working tree
```

---

## 8. Common Pitfalls

Things you might get wrong:

1. **Don't run `python -m pytest` directly.** Always use
   `venv\Scripts\python.exe -m pytest` so the venv is honored.

2. **Don't import the alignment module at the top of `omnivoice_worker.py`.**
   Use a function-local import (`from audio.tts_alignment import align_audio`
   inside `_maybe_align`) so the worker doesn't need faster-whisper just to
   start. The persistence server must boot even if alignment is broken.

3. **Don't put `print()` in the TTS worker — use the existing `log`.** The
   worker uses stdout-JSON for IPC, and `print()` calls outside the JSON
   envelope break the protocol. Use `log.warning()` etc. for diagnostics.

4. **Don't add type comments** like `# type: ignore[xxx]`. The project runs
   without mypy; adding type comments clutters the code.

5. **Don't add docstrings to existing functions** unless you wrote them.
   Existing code is comment-free. New code (your new files) may have a
   module-level docstring, but functions should not.

6. **The `requirements.txt` task is pinning, not installing.** Don't run
   `pip install` for the whole file — just append the lines. The venv already
   has these installed.

7. **`config_schemas.py` is plural.** Don't create `config_schema.py` (singular).

8. **Tests in `tests/conftest.py` reset `UIState` autouse.** If your new tests
   need a clean `UIState`, you get it for free. If you add a new `UIState`
   attribute elsewhere, you must also reset it in `conftest.py`.

---

## 9. Done Criteria (when to mark complete)

You are done when:

- [ ] All 13 tasks (0.1-0.8, 0.5.1-0.5.5) done
- [ ] `pytest tests/ -q` shows **all tests pass**, count went from 290 → 301+
- [ ] `ruff check .` shows **All checks passed!**
- [ ] Config keys load: `VideoAIConfig().critic.threshold == 60`
- [ ] TTS worker source contains `"word_timestamps"` literal in 4 places
- [ ] No new comments added to existing code
- [ ] No `import os` in `youtube_uploader.py`
- [ ] No `script-reviewer` references in `config.yaml` or `segment_runner.py`
- [ ] Two new test files: `tests/test_pipeline_graph.py` and
      `tests/test_tts_alignment.py`
- [ ] (Optional) Manual smoke test passes

**When complete, write a one-paragraph session log to the user summarizing:**
- How many files you touched (count)
- Test count before/after (290 → 30X)
- Any tasks you couldn't complete and why
- Any deviations from this brief

---

## 10. Files You Will Touch (summary)

| File | Type | Reason |
|---|---|---|
| `requirements.txt` | modify | Pin 4 deps |
| `config/config.yaml` | modify | Add 4 sections + 2 keys + remove reviewer |
| `config/config_schemas.py` | modify | Add 4 Pydantic models + 1 field |
| `utils/youtube_uploader.py` | modify | Bare except + unused import |
| `core/segment_runner.py` | modify | Stub out reviewer call |
| `audio/tts_alignment.py` | **new** | Alignment wrapper |
| `audio/omnivoice_worker.py` | modify | Add word_timestamps to output |
| `audio/f5_worker.py` | modify | Add word_timestamps to output |
| `video/renderer/assembler.py` | modify | Regression-detector WARNING |
| `tests/test_pipeline_graph.py` | **new** | 6 graph tests |
| `tests/test_tts_alignment.py` | **new** | 6 alignment tests |
| `tests/test_audio_crossfade.py` | modify | Assert word_timestamps populated |

**12 files total: 8 modified, 4 new.**
**Total estimated LoC:** ~110 production + ~200 tests = **~310 LoC**.
**Estimated time:** 3-5 hours.

---

End of brief. Start with `cd C:\Video.AI` and work through Task 0.1.
