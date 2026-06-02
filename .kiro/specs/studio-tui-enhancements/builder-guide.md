# Builder Guide — Studio TUI v2

Concrete patterns and gotchas for whoever implements `studio_tui.py`. Derived from
global skills: Python patterns, error handling, security, accessibility, UI polish,
and TDD. Read this before writing any code.

---

## 1. Python patterns to follow (from `python-patterns` skill)

### UIState additions — use `@classmethod` + type hints

```python
# agents/director_agent.py — additive additions only
import time

class UIState:
    # ... existing attrs unchanged ...

    # New additive fields (safe defaults — never raise if unset)
    segment_current: int = 0
    segment_total:   int = 0
    run_start_ts:    float = 0.0
    vram_text:       str = ""

    @classmethod
    def reset_run(cls, topic: str) -> None:
        """Call at run start. Zeroes progress so a 2nd run shows planning, not stale 12/12."""
        cls.topic           = topic
        cls.segment_current = 0
        cls.segment_total   = 0
        cls.run_start_ts    = time.time()
        cls.vram_text       = ""

    @classmethod
    def set_progress(cls, current: int | None = None, total: int | None = None) -> None:
        """Publish segment progress. Either arg optional; ignores None."""
        if total is not None:
            cls.segment_total = int(total)
        if current is not None:
            cls.segment_current = int(current)
```

### Pipeline hooks — use `pathlib.Path`, avoid bare `except`

```python
# core/pipeline_long.py — three additive writes

# 1. After outline reconciliation (n_segs is final):
UIState.set_progress(total=n_segs)

# 2. Inside the existing completed_segs_lock in process_segment's finally block:
with completed_segs_lock:
    completed_segs_counter += 1
    completed_segs = completed_segs_counter          # locked snapshot
    UIState.set_progress(current=completed_segs)     # publish inside lock — NEVER +1

# 3. In _log_vram_usage():
try:
    import torch
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        used_gb  = (total - free) / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        pct      = (1 - free / total) * 100
        UIState.vram_text = f"{used_gb:.1f}/{total_gb:.1f} GB ({pct:.0f}%)"
    else:
        UIState.vram_text = ""
except Exception:
    UIState.vram_text = ""   # never crash the pipeline for a display field
```

### Expose a zero-coupling cancel hook

```python
# core/pipeline_long.py — add this public function
def request_cancel() -> None:
    """Request graceful pipeline abort. Remaining segments skip; checkpoints preserved."""
    _director_set_abort(True)
```

### Elapsed + ETC — derive in the TUI, not the pipeline

```python
# studio_tui.py — computed in _poll, never stored
import time

def _format_elapsed(run_start_ts: float) -> str:
    if run_start_ts <= 0:
        return "—"
    secs = int(time.time() - run_start_ts)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _format_etc(current: int, total: int, run_start_ts: float) -> str:
    if total <= 0 or current <= 0 or run_start_ts <= 0:
        return "—"
    elapsed = time.time() - run_start_ts
    avg_per_seg = elapsed / current
    remaining   = avg_per_seg * (total - current)
    m, s = divmod(int(remaining), 60)
    return f"~{m}m {s}s" if m else f"~{s}s"
```

---

## 2. Error handling patterns (from `error-handling` skill)

### The poll loop — never swallow, never crash the interval

```python
# studio_tui.py
def _poll(self) -> None:
    try:
        self._do_poll()
    except Exception as exc:
        # Surface to log but keep the interval alive
        try:
            log = self.query_one("#log_container", RichLog)
            log.write(Text(f"✗ [poll error] {exc}", style=self._STYLE_ERROR))
        except Exception:
            pass  # log widget itself unavailable — truly silent fallback

def _do_poll(self) -> None:
    # ... all the real poll logic here ...
```

### Every new handler — specific exceptions, never bare `except`

```python
# Good pattern for all new action handlers
def action_open_output(self) -> None:
    output = getattr(UIState, "output_video", "")
    if not output:
        self.notify("No output yet", severity="warning")
        return
    try:
        import os
        from pathlib import Path
        os.startfile(Path(output).parent)
    except FileNotFoundError:
        self.notify("Output folder not found", severity="error")
    except OSError as exc:
        self.notify(f"Cannot open folder: {exc}", severity="error")
```

### File operations — EAFP style, chain exceptions

```python
# Task 14: reading a story file
def _read_story_file(self, path_str: str) -> str | None:
    from pathlib import Path
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except FileNotFoundError:
        self.notify(f"File not found: {path_str}", severity="error")
    except PermissionError:
        self.notify(f"Cannot read file (permission denied): {path_str}", severity="error")
    except OSError as exc:
        self.notify(f"File error: {exc}", severity="error")
    return None
```

---

## 3. Security patterns (from `security-review` skill)

### Path sanitization for checkpoint clear (Task 23)

The checkpoint `topic` comes from a filename on disk — it could contain `../` or
other traversal sequences if a file was manually placed there.

```python
# studio_tui.py — before calling CheckpointManager.clear(topic)
import re
from pathlib import Path

def _safe_checkpoint_topic(raw: str) -> str | None:
    """Return the topic only if it resolves safely inside studio_checkpoints/."""
    base = Path("studio_checkpoints").resolve()
    candidate = (base / raw).resolve()
    try:
        candidate.relative_to(base)   # raises ValueError if outside base
        return raw
    except ValueError:
        return None   # traversal attempt — reject silently

# Usage
safe = _safe_checkpoint_topic(selected_topic)
if safe is None:
    self.notify("Invalid checkpoint name", severity="error")
    return
cp_mgr.clear(safe)
```

### Input validation for the options form (Task 13)

```python
# Validate duration before starting a run
def _parse_duration(raw: str) -> int | None:
    """Return a positive int or None if invalid."""
    try:
        val = int(raw.strip())
        if val <= 0:
            return None
        return val
    except (ValueError, AttributeError):
        return None
```

### Never log UIState.user_reply verbatim

`user_reply` can contain operator-typed text including passwords or sensitive
story content. Log only its presence, not its value:

```python
# Good
log.info("[TUI] User reply received (%d chars)", len(UIState.user_reply or ""))

# Bad
log.info("[TUI] User reply: %s", UIState.user_reply)
```

---

## 4. Accessibility patterns (from `accessibility` skill)

### Focus management for modals (Task 18 confirm-quit, Task 16 pause)

Every `ModalScreen` must:
1. Trap focus while open (Textual does this automatically for `ModalScreen`).
2. Be dismissible with `Escape` (add a `Binding("escape", "dismiss")`).
3. Return focus to the widget that triggered it on close.

```python
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.widgets import Button, Static
from textual.app import ComposeResult

class ConfirmQuitModal(ModalScreen[bool]):
    """Confirm before quitting during an active run."""

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Static("A run is in progress. Quit anyway?", id="confirm_msg")
        yield Button("Quit", id="btn_quit",   variant="error")
        yield Button("Cancel", id="btn_cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn_quit")
```

### Icon + color, never color alone (Req 6.4, 14.3)

Every status indicator must have both a symbol and a color:

```python
STATUS_ICONS = {
    "idle":     ("●", "#7d8590"),   # grey dot
    "running":  ("⠋", "#58a6ff"),   # blue spinner
    "paused":   ("⏸", "#e3b341"),   # amber pause
    "complete": ("✓", "#7ee787"),   # green check
    "error":    ("✗", "#f85149"),   # red cross
}
```

### Keyboard-only operation (Req 15.1)

All new `Collapsible`, `Switch`, `Input`, and `Button` widgets must be in the
natural tab order. Textual handles this automatically — but verify by tabbing
through the Run tab in the PTY without touching the mouse.

---

## 5. UI polish patterns (from `make-interfaces-feel-better` skill)

### Tabular numbers for all counters and timers

In Textual CSS, use `font-variant-numeric: tabular-nums` on any widget that
shows a changing number (elapsed, segment count, VRAM). This prevents the layout
from jittering as digits change width.

```css
/* studio_tui.py CSS block */
#status_badge, #meta_line, .card {
    /* Textual doesn't support font-variant-numeric directly, but
       use fixed-width formatting in Python instead: */
    /* e.g. f"{elapsed:>8s}" or f"{cur:>3d}/{tot:>3d}" */
}
```

In Python, pad numbers to a fixed width:

```python
# Good — fixed width, no jitter
f"⏱ {elapsed:>8s}  │  seg {cur:>3d}/{tot:>3d}"

# Bad — variable width, layout shifts
f"⏱ {elapsed}  │  seg {cur}/{tot}"
```

### Concentric radius — panels inside panels

The Run tab has a `RichLog` panel inside a `TabPane` inside `TabbedContent`.
Keep the inner border radius consistent with the outer:

```css
TabbedContent {
    border: round #30363d;
    padding: 1;
}
#log_container {
    border: round #30363d;   /* same radius — optically coherent */
    padding: 0 1;
    margin: 0 1;
}
```

### Empty states — never blank, always `—`

Every Stats card must show `—` when data is unavailable, not an empty string:

```python
def _card_text(value: str, label: str, style: str) -> Text:
    display = value if value else "—"
    t = Text()
    t.append(f"{display}\n", style=f"bold {style}")
    t.append(label, style="#7d8590")
    return t
```

---

## 6. TDD workflow (from `tdd-workflow` skill)

### Write tests first for UIState helpers (Task 3.1)

Before writing `reset_run` / `set_progress`, write the tests:

```python
# tests/test_uistate.py
import pytest
from agents.director_agent import UIState

def setup_function():
    """Reset UIState before each test."""
    UIState.segment_current = 0
    UIState.segment_total   = 0
    UIState.run_start_ts    = 0.0
    UIState.vram_text       = ""

def test_set_progress_total_only():
    UIState.set_progress(total=12)
    assert UIState.segment_total   == 12
    assert UIState.segment_current == 0   # unchanged

def test_set_progress_current_only():
    UIState.segment_total = 12
    UIState.set_progress(current=3)
    assert UIState.segment_current == 3
    assert UIState.segment_total   == 12  # unchanged

def test_set_progress_ignores_none():
    UIState.segment_total   = 10
    UIState.segment_current = 5
    UIState.set_progress(current=None, total=None)
    assert UIState.segment_total   == 10  # unchanged
    assert UIState.segment_current == 5   # unchanged

def test_set_progress_casts_to_int():
    UIState.set_progress(total=8.9)   # float input
    assert UIState.segment_total == 8
    assert isinstance(UIState.segment_total, int)

def test_reset_run_zeroes_progress():
    UIState.segment_current = 5
    UIState.segment_total   = 12
    UIState.reset_run("My Topic")
    assert UIState.segment_current == 0
    assert UIState.segment_total   == 0
    assert UIState.topic            == "My Topic"
    assert UIState.run_start_ts     > 0

def test_reset_run_clears_vram():
    UIState.vram_text = "4.8/6.0 GB"
    UIState.reset_run("New Topic")
    assert UIState.vram_text == ""
```

Run: `venv\Scripts\python.exe -m pytest tests/test_uistate.py -v`

### TDD cycle for each task

1. **RED** — write the test, run it, confirm it fails.
2. **GREEN** — write the minimal code to pass.
3. **REFACTOR** — clean up, keep tests green.
4. `py_compile` the file.
5. PTY verify where the task has a "Verify:" step.

---

## 7. Quick reference: what to use where

| Task | Key skill/pattern |
|---|---|
| 1 (theme) | UI polish — concentric radius, no border on chrome |
| 3 (UIState) | Python patterns — `@classmethod`, type hints |
| 3.1 (tests) | TDD — write tests first, RED→GREEN |
| 4 (hooks) | Python patterns — locked snapshot, never `+1` |
| 6 (status) | UI polish — tabular numbers, icon+color |
| 8 (poll) | Error handling — never swallow, keep interval alive |
| 13 (options) | Security — validate duration input |
| 14 (file) | Error handling — EAFP, specific exceptions |
| 15 (cancel) | Python patterns — thin public wrapper |
| 16 (pause/cancel) | Accessibility — Escape-dismissible modals |
| 17 (output) | Error handling — specific OSError handling |
| 18 (QoL) | Accessibility — focus management on modal close |
| 23 (checkpoints) | Security — path traversal check before clear |
| All | Error handling — never bare `except:`, always log |
