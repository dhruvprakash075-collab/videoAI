#!/usr/bin/env python3
"""
studio_tui.py - Studio Console TUI (Phase 1 + 2 + 3)

Multi-view Textual terminal control panel for the Video.AI pipeline.
Tabs: Run | Stats | Help  +  Screens: Checkpoints | Artifacts | Preflight
"""

import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path as _Path
from typing import Any, cast

_CRASH_LOG = str(_Path(__file__).resolve().parent / "studio_tui_crash.log")
_PROJECT_ROOT = _Path(__file__).resolve().parent


def _crash_log(exc_type, exc, tb):
    try:
        with open(_CRASH_LOG, "w", encoding="utf-8") as f:
            import traceback as _tb
            _tb.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _crash_log

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.theme import Theme
    from textual.widgets import (
        Button,
        Collapsible,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        ProgressBar,
        RichLog,
        Sparkline,
        Static,
        Switch,
        TabbedContent,
        TabPane,
    )
except ImportError:
    print("Textual not installed. Run: venv\\Scripts\\python.exe -m pip install textual")
    sys.exit(1)

import contextlib

from agents.director_agent import UIState

# ── Pure helpers (single source of truth in studio_tui_helpers.py) ────────────
from studio_tui_helpers import (
    format_elapsed as _format_elapsed,
    format_etc as _format_etc,
    parse_duration as _parse_duration,
    vram_high as _vram_high,
)

# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
/* ── Modern Video.AI Tokyo Night Studio TUI ── */
Screen { background: #0d0e15; }
Header { background: #161622; color: #82a1ff; height: 1; }
Footer { background: #161622; color: #4b526d; height: 1; }
TabbedContent { background: #0d0e15; height: 1fr; }
TabPane { background: #0d0e15; height: 1fr; }

/* Status bar */
#status_badge { background: #161622; color: #c0caf5; height: 1; padding: 0 1; }
#status_meta  { background: #0d0e15; color: #4b526d; height: 1; padding: 0 1; }
#seg_progress { margin: 0 1; height: 1; }

/* Custom dynamic loading bar styling */
ProgressBarBar {
    background: #1f2335;
    color: #73daca;
}

/* Log — fills all remaining space */
#log_container {
    background: #0d0e15;
    border: solid #2f3549;
    padding: 0 1;
    margin: 0 1;
    height: 1fr;
}

#log_container:focus {
    border: solid #7aa2f7;
}

/* Run tab vertical layout — log must fill remaining height */
#run > Vertical {
    height: 1fr;
}

/* Input composer */
#composer {
    background: #161622;
    border: solid #2f3549;
    margin: 0 1;
    height: 3;
}
Input { background: #161622; color: #c0caf5; }
Input:focus { border: solid #7aa2f7; }

/* Run options */
#opts_collapsible { margin: 0 1; }
#opts_row1, #opts_row2, #opts_row3 { height: 3; }
#opt_duration { width: 12; }
#opt_project  { width: 20; }
#opt_file     { width: 40; }
.opt_label    { width: 14; height: 1; color: #4b526d; }
.opt_switch   { width: 8; }

/* Stats grid */
#stats_grid { grid-size: 2 2; grid-gutter: 1 2; margin: 1; }
.stats_card {
    background: #1a1b26;
    border: solid #2f3549;
    padding: 1;
    height: 7;
}

/* Web-like responsive hover effects */
.stats_card:hover {
    border: solid #7aa2f7;
    background: #1f2335;
}

/* Help */
#help_text { background: #0d0e15; color: #8b949e; padding: 1 2; }

/* Resize warning */
#resize_notice {
    background: #0d0e15; color: #f7768e;
    content-align: center middle; display: none;
}

/* Modal */
ConfirmModal { align: center middle; }
#confirm_box {
    background: #1a1b26; border: solid #2d3748;
    padding: 2 4; width: 60; height: 10;
}
#confirm_msg { color: #c0caf5; margin-bottom: 1; }
#confirm_btns { height: 3; }
#btn_yes { background: #8b2020; color: #fff; margin-right: 2; width: 12; }
#btn_no  { background: #1e2530; color: #8b949e; width: 12; }

/* Screens */
.screen_title { color: #7aa2f7; height: 1; padding: 0 1; margin-bottom: 1; }
.screen_hint  { color: #4b526d; height: 1; padding: 0 1; }
DataTable { background: #0d0e15; }
"""

# ── Help text ─────────────────────────────────────────────────────────────────

_HELP_TEXT = """\
  Video.AI Studio Console — Keyboard Reference

  Navigation        f1 Help   f2 Run   f3 Stats
  Log               Ctrl+L  Clear     Ctrl+E  Scroll to end
  Run control       f5 Pause          Ctrl+X  Cancel run
  Output            Ctrl+O  Open folder       Ctrl+Y  Copy path
  Session log       Ctrl+W  Save log to file
  Screens           Ctrl+K  Checkpoints       Ctrl+R  Artifacts
                    f6 Preflight              Ctrl+P  Command palette
  Quit              Ctrl+Q  or  Ctrl+C

  Run Options (expand the panel above the input):
    Duration (min), Resume, Skip-RVC, Director mode,
    Preview mode, Project name, Story file path

  Tip: run via TUI.bat — not the embedded editor terminal.
"""

# ── Confirm modal (Phase 2) ───────────────────────────────────────────────────

class ConfirmModal(ModalScreen):
    """Generic yes/no confirmation dialog."""

    def __init__(self, message: str, on_confirm):
        super().__init__()
        self._message = message
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static(self._message, id="confirm_msg")
            with Horizontal(id="confirm_btns"):
                yield Button("Yes", id="btn_yes", variant="error")
                yield Button("No",  id="btn_no",  variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss()
            self._on_confirm()
        else:
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


# ── Checkpoints screen (Phase 3) ─────────────────────────────────────────────

class CheckpointsScreen(ModalScreen):
    """Read-only list of resumable checkpoints with clear action."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("  ◈ Checkpoints", classes="screen_title")
            yield Static("  Enter=Resume  d=Clear  Escape=Close", classes="screen_hint")
            yield DataTable(id="cp_table")

    def on_mount(self) -> None:
        table = self.query_one("#cp_table", DataTable)
        table.add_columns("Topic", "Age", "Steps", "Stale?")
        self._load(table)

    def _load(self, table: DataTable) -> None:
        table.clear()
        cp_dir = _PROJECT_ROOT / "studio_checkpoints"
        if not cp_dir.exists():
            return
        now = time.time()
        for p in sorted(cp_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                import json as _j
                data = _j.loads(p.read_text(encoding="utf-8"))
                steps = str(len(data))
                age_h = (now - p.stat().st_mtime) / 3600
                age_str = f"{age_h:.1f}h"
                stale = "⚠ yes" if age_h > 48 else "no"
                topic = p.stem
                table.add_row(topic, age_str, steps, stale, key=topic)
            except Exception:
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        topic = str(event.row_key.value)
        self.dismiss()
        # Post a message back to the app to start a resume run
        cast("StudioTUI", self.app)._resume_from_checkpoint(topic)

    def on_key(self, event) -> None:
        if event.key == "d":
            table = self.query_one("#cp_table", DataTable)
            if table.cursor_row is not None:
                try:
                    row_key = table.get_row_at(table.cursor_row)[0]
                    topic = str(row_key) if row_key else None
                    if topic:
                        def do_clear():
                            try:
                                from utils.checkpoint import CheckpointManager
                                CheckpointManager().clear(topic)
                                self._load(table)
                                self.app.notify(f"Checkpoint cleared: {topic}")
                            except Exception as e:
                                self.app.notify(f"Clear failed: {e}", severity="error")
                        self.app.push_screen(ConfirmModal(
                            f"Clear checkpoint for '{topic}'?", do_clear))
                except Exception:
                    pass


# ── Artifacts screen (Phase 3) ────────────────────────────────────────────────

class ArtifactsScreen(ModalScreen):
    """Read-only viewer for run_manifest.json, segment meta, chapters."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("  ◈ Run Artifacts", classes="screen_title")
            yield Static("  Escape=Close", classes="screen_hint")
            yield RichLog(id="artifact_log", auto_scroll=False)

    def on_mount(self) -> None:
        log = self.query_one("#artifact_log", RichLog)
        self._load(log)

    def _load(self, log: RichLog) -> None:
        import json as _j
        out_dir = _PROJECT_ROOT / "studio_outputs"
        if not out_dir.exists():
            log.write(Text("  No studio_outputs directory found.", style="#7d8590"))
            return
        # Find most recent run manifest
        manifests = sorted(out_dir.rglob("run_manifest.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if not manifests:
            log.write(Text("  No run_manifest.json found yet.", style="#7d8590"))
            return
        manifest_path = manifests[0]
        log.write(Text(f"  Run: {manifest_path.parent.name}", style="#58a6ff"))
        try:
            data = _j.loads(manifest_path.read_text(encoding="utf-8"))
            models = data.get("models", {})
            log.write(Text(f"  Director: {models.get('director','—')}  Writer: {models.get('writer','—')}", style="#e6edf3"))
            segs = data.get("segments_completed", "—")
            dur  = data.get("duration_s", 0)
            log.write(Text(f"  Segments: {segs}  Duration: {dur:.0f}s", style="#e6edf3"))
            quality = data.get("quality", {})
            if quality:
                passed = quality.get("passed", False)
                issues = quality.get("issues", [])
                color = "#7ee787" if passed else "#f85149"
                log.write(Text(f"  Quality: {'PASS' if passed else 'FAIL'}  Issues: {len(issues)}", style=color))
        except Exception as e:
            log.write(Text(f"  manifest error: {e}", style="#f85149"))
        # Chapters
        chapters = manifest_path.parent / "chapters.txt"
        if chapters.exists():
            log.write(Text("  ── chapters.txt ──", style="#7d8590"))
            try:
                for line in chapters.read_text(encoding="utf-8").splitlines()[:20]:
                    log.write(Text(f"  {line}", style="#e6edf3"))
            except Exception:
                pass
        # Segment metas
        seg_metas = sorted(manifest_path.parent.rglob("segment_*_meta.json"))[:5]
        if seg_metas:
            log.write(Text("  ── Segment meta (first 5) ──", style="#7d8590"))
            for sm in seg_metas:
                try:
                    d = _j.loads(sm.read_text(encoding="utf-8"))
                    mood = d.get("mood", "—")
                    words = d.get("word_count", "—")
                    oom = d.get("oom", False)
                    oom_str = " ⚠OOM" if oom else ""
                    log.write(Text(f"  {sm.stem}: mood={mood} words={words}{oom_str}", style="#e6edf3"))
                except Exception:
                    pass


# ── Preflight screen (Phase 3) ────────────────────────────────────────────────

class PreflightScreen(ModalScreen):
    """Shows preflight health checks. 'r' re-runs them on a background thread."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("r", "rerun", "Re-run checks"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("  ◈ Preflight Health Checks  (r=Re-run  Escape=Close)", classes="screen_title")
            yield DataTable(id="pf_table")

    def on_mount(self) -> None:
        table = self.query_one("#pf_table", DataTable)
        table.add_columns("Check", "Status", "Detail")
        table.add_row("Running checks", "… WAIT", "Contacting Ollama / checking disk…")
        # Run in a background thread so network I/O doesn't block the UI and
        # call_from_thread works correctly (it must be called off the UI thread).
        threading.Thread(target=self._run_checks, args=(table,), daemon=True).start()

    def action_rerun(self) -> None:
        table = self.query_one("#pf_table", DataTable)
        table.clear()
        table.add_row("Running checks", "… WAIT", "Re-checking…")
        self.app.notify("Re-running preflight checks…")
        threading.Thread(target=self._run_checks, args=(table,), daemon=True).start()

    def _run_checks(self, table: DataTable) -> None:
        try:
            from utils import load_config
            config = load_config()
            checks = _collect_preflight(config)
        except Exception as e:
            checks = {"Preflight": {"status": "FAILED", "info": str(e)}}

        def _update():
            table.clear()
            for name, result in checks.items():
                st = result.get("status", "?")
                icon = "✓" if st == "OK" else ("⚠" if st == "WARN" else "✗")
                color = "#7ee787" if st == "OK" else ("#f0c040" if st == "WARN" else "#f85149")
                table.add_row(
                    Text(name, style="#e6edf3"),
                    Text(f"{icon} {st}", style=color),
                    Text(result.get("info", ""), style="#7d8590"),
                )
        with contextlib.suppress(Exception):
            self.app.call_from_thread(_update)


def _collect_preflight(config: dict) -> dict:
    """Pure collector — returns dict of check results without logging."""
    import json as _j
    import shutil
    import urllib.request
    checks = {}
    ollama_host = config.get("ollama", {}).get("host", "http://localhost:11434")
    director_model = config.get("models", {}).get("director", "hermes-director")
    writer_model   = config.get("models", {}).get("writer",   "zephyr-writer")
    tts_engine     = config.get("tts", {}).get("engine", "omnivoice")

    # FFmpeg
    ffmpeg = shutil.which("ffmpeg")
    checks["FFmpeg"] = {"status": "OK" if ffmpeg else "FAILED",
                        "info": ffmpeg or "NOT FOUND on PATH"}
    # Disk
    try:
        _, _, free = shutil.disk_usage(".")
        free_gb = free / (1024**3)
        checks["Disk Space"] = {"status": "OK" if free_gb > 10 else "WARN",
                                 "info": f"{free_gb:.1f} GB free"}
    except Exception as e:
        checks["Disk Space"] = {"status": "FAILED", "info": str(e)}
    # TTS
    if tts_engine == "edge":
        try:
            import edge_tts
            checks[f"TTS ({tts_engine})"] = {"status": "OK", "info": "edge-tts installed"}
        except ImportError:
            checks[f"TTS ({tts_engine})"] = {"status": "FAILED", "info": "edge-tts not installed"}
    else:
        worker = _PROJECT_ROOT / "audio" / "omnivoice_worker.py"
        checks[f"TTS ({tts_engine})"] = {
            "status": "OK" if worker.exists() else "WARN",
            "info": str(worker) if worker.exists() else "omnivoice_worker.py not found"
        }
    # Ollama
    try:
        req = urllib.request.Request(f"{ollama_host}/api/tags",
                                     headers={"User-Agent": "Video.AI TUI"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = _j.loads(resp.read().decode())
        tags = [t["name"] for t in data.get("models", [])]
        checks["Ollama"] = {"status": "OK", "info": f"Connected {ollama_host}"}
        for m, label in [(director_model, "Director"), (writer_model, "Writer")]:
            found = any(m in t for t in tags)
            checks[f"Model {label}"] = {
                "status": "OK" if found else "WARN",
                "info": m if found else f"Not pulled — run: ollama pull {m}"
            }
    except Exception as e:
        checks["Ollama"] = {"status": "FAILED", "info": str(e)}
        checks["Model Director"] = {"status": "FAILED", "info": "Ollama unreachable"}
        checks["Model Writer"]   = {"status": "FAILED", "info": "Ollama unreachable"}
    return checks


# ── Main App ──────────────────────────────────────────────────────────────────

class StudioTUI(App):
    """Video.AI Studio Console — Phase 1 + 2 + 3."""

    CSS = _CSS
    TITLE = "Video.AI Studio Console"

    BINDINGS = [
        Binding("ctrl+q,ctrl+c", "quit",        "Quit",        priority=True),
        Binding("ctrl+l",        "clear_log",   "Clear Log",   priority=True),
        Binding("ctrl+e",        "scroll_end",  "Scroll End",  priority=True),
        Binding("f1",            "show_help",   "Help",        priority=True),
        Binding("f2",            "show_run",    "Run",         priority=True),
        Binding("f3",            "show_stats",  "Stats",       priority=True),
        Binding("f5",            "pause_run",   "Pause",       priority=True),
        Binding("ctrl+x",        "cancel_run",  "Cancel",      priority=True),
        Binding("ctrl+o",        "open_output", "Open Output", priority=True),
        Binding("ctrl+y",        "copy_output", "Copy Path",   priority=True),
        Binding("ctrl+w",        "save_log",    "Save Log",    priority=True),
        Binding("ctrl+k",        "show_checkpoints", "Checkpoints", priority=True),
        Binding("ctrl+r",        "show_artifacts",   "Artifacts",   priority=True),
        Binding("f6",            "show_preflight",   "Preflight",   priority=True),
    ]

    POLL_INTERVAL_S: float = 0.4
    _STYLE_AGENT  = "#7aa2f7"
    _STYLE_USER   = "#73daca"
    _STYLE_ERROR  = "#f7768e"
    _STYLE_SYSTEM = "#565f89"

    def __init__(self):
        super().__init__()
        self.log_index: int = 0
        self.spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spinner_index: int = 0
        self._pipeline_active: bool = False
        self._last_status: str = ""
        self._last_question: str = ""
        self._throughput_samples: list = []
        self._last_seg_current: int = 0
        # B2 perf: cache the RESUMABLE check so _poll_status doesn't read+parse
        # config.yaml from disk on every 0.4s poll. Re-checked only when the topic
        # changes (every ~30 polls as a safety refresh).
        self._resumable_cache: str = ""
        self._resumable_topic: str = ""
        self._resumable_poll_count: int = 0
        # Phase 2 option state
        self._opt_duration: str = ""
        self._opt_resume: bool = True
        self._opt_skip_rvc: bool = False
        self._opt_director: bool = False
        self._opt_preview: bool = False
        self._opt_project: str = ""
        self._opt_use_file: bool = False
        self._opt_file: str = ""

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="resize_notice")
        with TabbedContent(initial="run"):
            with TabPane("Run", id="run"):
                with Vertical():
                    yield Static("", id="status_badge")
                    yield Static("", id="status_meta")
                    yield ProgressBar(total=100, show_eta=False, id="seg_progress")
                    # Phase 2: collapsible run options
                    with Collapsible(title="Run options", id="opts_collapsible", collapsed=True):
                        with Vertical():
                            with Horizontal(id="opts_row1"):
                                yield Label("Duration(min)", classes="opt_label")
                                yield Input(placeholder="10", id="opt_duration", classes="opt_input")
                                yield Label("Project", classes="opt_label")
                                yield Input(placeholder="(optional)", id="opt_project", classes="opt_input")
                            with Horizontal(id="opts_row2"):
                                yield Label("Resume", classes="opt_label")
                                yield Switch(value=True, id="opt_resume")
                                yield Label("Skip-RVC", classes="opt_label")
                                yield Switch(value=False, id="opt_skip_rvc")
                                yield Label("Director", classes="opt_label")
                                yield Switch(value=False, id="opt_director")
                                yield Label("Preview", classes="opt_label")
                                yield Switch(value=False, id="opt_preview")
                            with Horizontal(id="opts_row3"):
                                yield Label("Story file", classes="opt_label")
                                yield Switch(value=False, id="opt_use_file")
                                yield Input(placeholder="path\\to\\story.txt", id="opt_file", classes="opt_input")
                    yield RichLog(id="log_container", auto_scroll=True)
                    yield Input(placeholder="Enter topic — or reply to Director pause…", id="composer")
            with TabPane("Stats", id="stats"):
                from textual.containers import Grid
                with Grid(id="stats_grid"):
                    yield Static("", id="card_elapsed", classes="stats_card")
                    yield Static("", id="card_segments", classes="stats_card")
                    with Vertical(classes="stats_card"):
                        yield Static("throughput", id="card_throughput_label")
                        yield Sparkline([], id="card_sparkline")
                    yield Static("", id="card_engines", classes="stats_card")
            with TabPane("Help", id="help"):
                yield Static(_HELP_TEXT, id="help_text")
        yield Footer()

    def on_mount(self) -> None:
        UIState.status = "idle"
        UIState.is_ui_mode = True
        self.set_interval(self.POLL_INTERVAL_S, self._poll)
        with contextlib.suppress(Exception):
            self.query_one("#log_container", RichLog).write(
                Text("● Studio Console ready — enter a topic to start", style=self._STYLE_AGENT))
        # Focus the composer so typed input lands in it (TabbedContent steals focus otherwise)
        with contextlib.suppress(Exception):
            self.set_focus(self.query_one("#composer", Input))

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            self._poll_logs(); self._poll_status(); self._poll_progress()
            self._poll_stats(); self._poll_question(); self._poll_bell()
        except Exception as e:
            with contextlib.suppress(Exception):
                self.query_one("#log_container", RichLog).write(
                    Text(f"  [poll error] {e}", style=self._STYLE_ERROR))

    def _poll_logs(self) -> None:
        logs = getattr(UIState, "logs", [])
        if len(logs) < self.log_index:
            self.log_index = 0
        if not logs:
            return
        w = self.query_one("#log_container", RichLog)
        while self.log_index < len(logs):
            self._add_log_message(w, logs[self.log_index])
            self.log_index += 1

    def _poll_status(self) -> None:
        status = getattr(UIState, "status", "idle")
        topic  = getattr(UIState, "topic", "")
        output = getattr(UIState, "output_video", "")
        seg_c  = getattr(UIState, "segment_current", 0)
        seg_t  = getattr(UIState, "segment_total", 0)
        vram   = getattr(UIState, "vram_text", "")
        ts     = getattr(UIState, "run_start_ts", 0.0)

        # B2: degradation count badge
        _deg_count = len(getattr(UIState, "degradations", []))
        _deg_badge = f"  ⚠ {_deg_count} degraded" if _deg_count else ""

        # B2: RESUMABLE indicator — cached so we don't read+parse config.yaml from
        # disk on every poll. Only re-check when the topic changes (or every ~30
        # polls ≈ 12s as a safety refresh in case a checkpoint appears mid-run).
        self._resumable_poll_count += 1
        if topic != self._resumable_topic or self._resumable_poll_count >= 30:
            self._resumable_topic = topic
            self._resumable_poll_count = 0
            self._resumable_cache = ""
            if topic:
                try:
                    from config import load_config as _lc
                    from utils.checkpoint import build_checkpoint_manager
                    _cp = build_checkpoint_manager(_lc())
                    if _cp.get(topic) is not None:
                        self._resumable_cache = "  ↩ RESUMABLE"
                except Exception:
                    pass
        _resumable = self._resumable_cache

        if status == "running":
            self.spinner_index = (self.spinner_index + 1) % len(self.spinner_chars)
            icon = self.spinner_chars[self.spinner_index]
            badge = f"{icon} RUNNING  •  {topic}" if topic else f"{icon} RUNNING"
        elif status == "paused":
            badge = f"⏸ PAUSED  •  {topic}" if topic else "⏸ PAUSED"
        elif status == "complete":
            badge = f"✓ COMPLETE  •  {topic}" if topic else "✓ COMPLETE"
        elif status == "error":
            badge = f"✗ ERROR  •  {topic}" if topic else "✗ ERROR"
        else:
            badge = "● IDLE — enter a topic to start"
        with contextlib.suppress(Exception):
            self.query_one("#status_badge", Static).update(badge)
        elapsed  = _format_elapsed(ts)
        etc      = _format_etc(ts, seg_c, seg_t)
        seg_str  = f"{seg_c}/{seg_t}" if seg_t else "—"
        vram_str = vram if vram else "—"
        if status == "complete" and output:
            meta = f"  Output: {output}{_deg_badge}"
        elif status == "error":
            meta = f"  ⏱ {elapsed}  │  seg {seg_str}  │  VRAM {vram_str}  │  ✗ error{_deg_badge}"
        else:
            meta = f"  ⏱ {elapsed}  │  seg {seg_str}  │  ETC {etc}  │  VRAM {vram_str}{_deg_badge}{_resumable}"
        with contextlib.suppress(Exception):
            self.query_one("#status_meta", Static).update(meta)

    def _poll_progress(self) -> None:
        seg_c  = getattr(UIState, "segment_current", 0)
        seg_t  = getattr(UIState, "segment_total", 0)
        status = getattr(UIState, "status", "idle")
        try:
            bar = self.query_one("#seg_progress", ProgressBar)
            if status not in ("running", "paused", "complete"):
                bar.update(total=None); return
            if seg_t == 0:
                bar.update(total=None)
            elif status == "complete":
                bar.update(total=seg_t, progress=seg_t)
            else:
                bar.update(total=seg_t, progress=seg_c)
        except Exception:
            pass

    def _poll_stats(self) -> None:
        seg_c = getattr(UIState, "segment_current", 0)
        seg_t = getattr(UIState, "segment_total", 0)
        vram  = getattr(UIState, "vram_text", "")
        ts    = getattr(UIState, "run_start_ts", 0.0)
        logs  = getattr(UIState, "logs", [])
        elapsed  = _format_elapsed(ts)
        etc      = _format_etc(ts, seg_c, seg_t)
        pct      = f"{int(seg_c/seg_t*100)}%" if seg_t else "—"
        last_log = logs[-1][:50] if logs else "—"
        if seg_c > self._last_seg_current and ts and seg_c > 0:
            elapsed_s = max(0.001, time.time() - ts)
            self._throughput_samples.append(elapsed_s / seg_c)
            if len(self._throughput_samples) > 20:
                self._throughput_samples = self._throughput_samples[-20:]
        self._last_seg_current = seg_c
        with contextlib.suppress(Exception):
            self.query_one("#card_elapsed", Static).update(
                f"[bold #4a90d9]Elapsed[/]\n\n  {elapsed}\n  ETC: {etc}")
        with contextlib.suppress(Exception):
            self.query_one("#card_segments", Static).update(
                f"[bold #4a90d9]Segments[/]\n\n  {seg_c} / {seg_t if seg_t else '—'}\n  {pct}")
        try:
            sp = self.query_one("#card_sparkline", Sparkline)
            if self._throughput_samples:
                sp.data = self._throughput_samples
        except Exception:
            pass
        vram_color = "#e05252" if vram and _vram_high(vram) else "#5a9e6f"
        with contextlib.suppress(Exception):
            self.query_one("#card_engines", Static).update(
                f"[bold #4a90d9]Engines / VRAM[/]\n\n"
                f"  [{vram_color}]{vram if vram else '—'}[/]\n"
                f"  [dim]{last_log}[/]")

    def _poll_question(self) -> None:
        question = getattr(UIState, "active_question", None)
        if question and question != self._last_question:
            self._last_question = question
            try:
                self.query_one("#log_container", RichLog).write(
                    Text(f"● {question}", style=self._STYLE_AGENT))
                self.query_one("#composer", Input).focus()
            except Exception:
                pass

    def _poll_bell(self) -> None:
        status = getattr(UIState, "status", "idle")
        if status in ("complete", "error") and status != self._last_status:
            self.bell()
            if status == "complete":
                self.notify("Run complete!", severity="information")
            else:
                self.notify("Run ended with error", severity="error")
        self._last_status = status

    # ── Log styling ───────────────────────────────────────────────────────────

    def _add_log_message(self, log: RichLog, msg: str) -> None:
        if msg.startswith("[DIRECTOR"):
            log.write(Text(f"● {msg}", style=self._STYLE_AGENT))
        elif "ERROR" in msg.upper() or "FATAL" in msg.upper():
            log.write(Text(f"✗ {msg}", style=self._STYLE_ERROR))
        elif msg.startswith(("▸", "SUCCESS")):
            log.write(Text(msg, style=self._STYLE_USER))
        else:
            log.write(Text(f"  {msg}", style=self._STYLE_SYSTEM))

    # ── Input ─────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "composer":
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        if getattr(UIState, "status", "idle") == "paused":
            with contextlib.suppress(Exception):
                self.query_one("#log_container", RichLog).write(
                    Text(f"▸ {text}", style=self._STYLE_USER))
            UIState.user_reply = text
            self._last_question = ""
            if hasattr(UIState, "pause_event") and UIState.pause_event:
                UIState.pause_event.set()
            return

        if self._pipeline_active:
            self.notify("Pipeline already running", severity="warning"); return

        # Build kwargs from options
        kwargs: dict[str, Any] = {}
        dur = _parse_duration(self._opt_duration)
        if dur:
            kwargs["duration_min"] = dur
        if not self._opt_resume:
            kwargs["resume"] = False
        if self._opt_skip_rvc:
            kwargs["skip_rvc"] = True
        if self._opt_director:
            kwargs["director_mode"] = True
        if self._opt_preview:
            kwargs["preview_mode"] = True
        if self._opt_project.strip():
            kwargs["project_name"] = self._opt_project.strip()

        content_text = None
        topic = text

        if self._opt_use_file:
            file_path_str = self._opt_file.strip() if hasattr(self, "_opt_file") else ""
            if not file_path_str:
                self.notify("Story file path is empty", severity="error"); return
            try:
                candidate = _Path(file_path_str).resolve()
                if not str(candidate).startswith(str(_PROJECT_ROOT)):
                    self.notify("File path must be inside the project folder", severity="error"); return
                if not candidate.exists():
                    self.notify(f"File not found: {candidate}", severity="error"); return
                content_text = candidate.read_text(encoding="utf-8")
                topic = candidate.stem
                kwargs["content_text"] = content_text
            except Exception as e:
                self.notify(f"Cannot read file: {e}", severity="error"); return

        with contextlib.suppress(Exception):
            self.query_one("#log_container", RichLog).write(
                Text(f"▸ Starting: {topic}  {kwargs if kwargs else ''}", style=self._STYLE_USER))

        try:
            from core.pipeline_long import _director_set_abort
            _director_set_abort(False)
        except Exception:
            pass

        UIState.is_ui_mode = True
        UIState.logs = []
        UIState.active_question = None
        UIState.user_reply = None
        UIState.output_video = ""
        UIState.pause_event = threading.Event()
        # Unless Director (interactive) mode is ON, auto-accept all Director
        # questions so a normal run flows straight through without pausing.
        # This is what made runs look "frozen/crashed" — they were silently
        # waiting at a Director pause for an answer that never came.
        UIState.auto_accept = not self._opt_director
        UIState.reset_run(topic)
        self.log_index = 0
        self._last_question = ""
        self._last_status = ""
        self._throughput_samples = []
        self._last_seg_current = 0
        self._pipeline_active = True
        self._start_pipeline_thread(topic, kwargs)
        self.notify(f"Run started: {topic}", severity="information")
    def on_switch_changed(self, event: Switch.Changed) -> None:
        sid = event.switch.id
        if sid == "opt_resume":     self._opt_resume    = event.value
        elif sid == "opt_skip_rvc": self._opt_skip_rvc  = event.value
        elif sid == "opt_director": self._opt_director  = event.value
        elif sid == "opt_preview":  self._opt_preview   = event.value
        elif sid == "opt_use_file": self._opt_use_file  = event.value

    def on_input_changed(self, event: Input.Changed) -> None:
        iid = event.input.id
        if iid == "opt_duration": self._opt_duration = event.value
        elif iid == "opt_project": self._opt_project = event.value
        elif iid == "opt_file":    self._opt_file    = event.value

    def _start_pipeline_thread(self, topic: str, kwargs: dict) -> None:
        def run():
            try:
                from core.pipeline_long import run_long_pipeline
                result = run_long_pipeline(topic=topic, **kwargs)
                status = result.get("status", "unknown")
                if status == "success":
                    UIState.output_video = result.get("output", "")
                    UIState.status = "complete"
                    UIState.add_log(f"SUCCESS: {UIState.output_video}")
                else:
                    reason = result.get("reason") or result.get("error") or status
                    UIState.status = "error"
                    UIState.add_log(f"Pipeline ended: {reason}")
            except Exception as e:
                import traceback as _tb
                UIState.status = "error"
                UIState.add_log(f"FATAL ERROR: {type(e).__name__}: {e}")
                # Write full traceback to crash log for debugging
                try:
                    with open(_CRASH_LOG, "a", encoding="utf-8") as f:
                        _tb.print_exc(file=f)
                except Exception:
                    pass
            finally:
                self._pipeline_active = False
        threading.Thread(target=run, daemon=True).start()

    def _resume_from_checkpoint(self, topic: str) -> None:
        """Called by CheckpointsScreen after user selects a checkpoint."""
        if self._pipeline_active:
            self.notify("Pipeline already running", severity="warning"); return
        UIState.is_ui_mode = True
        UIState.logs = []
        UIState.active_question = None
        UIState.user_reply = None
        UIState.output_video = ""
        UIState.pause_event = threading.Event()
        UIState.auto_accept = not self._opt_director
        UIState.reset_run(topic)
        self.log_index = 0
        self._last_question = ""
        self._last_status = ""
        self._throughput_samples = []
        self._last_seg_current = 0
        self._pipeline_active = True
        self._start_pipeline_thread(topic, {"resume": True})
        self.notify(f"Resuming: {topic}", severity="information")

    # ── Resize guard ──────────────────────────────────────────────────────────

    def on_resize(self, event) -> None:
        try:
            notice = self.query_one("#resize_notice", Static)
            if event.size.width < 80 or event.size.height < 24:
                notice.update("  ⚠  Resize terminal to at least 80×24  ⚠")
                notice.styles.display = "block"
                notice.styles.height = "100%"
            else:
                notice.styles.display = "none"
        except Exception:
            pass

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_clear_log(self) -> None:
        try:
            log = self.query_one("#log_container", RichLog)
            log.clear()
            self.log_index = len(getattr(UIState, "logs", []))
            log.write(Text("● Log cleared", style=self._STYLE_SYSTEM))
        except Exception:
            pass

    def action_scroll_end(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#log_container", RichLog).scroll_end(animate=False)

    def action_show_help(self) -> None:
        try:
            self.set_focus(None)
            self.query_one(TabbedContent).active = "help"
        except Exception: pass

    def action_show_run(self) -> None:
        try:
            self.query_one(TabbedContent).active = "run"
            self._restore_composer_focus()
        except Exception: pass

    def action_show_stats(self) -> None:
        try:
            self.set_focus(None)
            self.query_one(TabbedContent).active = "stats"
        except Exception: pass

    def action_pause_run(self) -> None:
        if not self._pipeline_active:
            self.notify("No run active", severity="warning"); return
        UIState.status = "paused"
        UIState.active_question = "Manual pause — type a reply to resume, or press Enter to continue."
        if hasattr(UIState, "pause_event"):
            UIState.pause_event.clear()
        self.notify("Run paused", severity="warning")

    def action_cancel_run(self) -> None:
        if not self._pipeline_active:
            self.notify("No run active", severity="warning"); return
        def do_cancel():
            try:
                from core.pipeline_long import request_cancel
                request_cancel()
            except Exception:
                pass
            # If the pipeline is blocked on a Director pause, release it so the
            # abort actually takes effect instead of hanging until the 600s timeout.
            try:
                if getattr(UIState, "status", "") == "paused" and getattr(UIState, "pause_event", None):
                    UIState.user_reply = "quit"
                    UIState.pause_event.set()
            except Exception:
                pass
            self._last_question = ""
            self.notify("Cancel requested — stopping run…", severity="warning")
            self._restore_composer_focus()
        self.push_screen(ConfirmModal("Cancel the current run? (checkpoints preserved)", do_cancel))

    def _restore_composer_focus(self) -> None:
        """Return focus to the composer after a modal/screen closes."""
        with contextlib.suppress(Exception):
            self.set_focus(self.query_one("#composer", Input))

    def action_open_output(self) -> None:
        output = getattr(UIState, "output_video", "")
        if not output:
            self.notify("No output path yet", severity="warning"); return
        try:
            folder = _Path(output).parent
            os.startfile(str(folder))
            self.notify(f"Opened: {folder}")
        except Exception as e:
            self.notify(f"Cannot open folder: {e}", severity="error")

    def action_copy_output(self) -> None:
        output = getattr(UIState, "output_video", "")
        logs   = getattr(UIState, "logs", [])
        value  = output or (logs[-1] if logs else "")
        if not value:
            self.notify("Nothing to copy", severity="warning"); return
        try:
            self.copy_to_clipboard(value)
            self.notify(f"Copied: {value[:60]}")
        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def action_save_log(self) -> None:
        logs = getattr(UIState, "logs", [])
        if not logs:
            self.notify("Log is empty", severity="warning"); return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = _PROJECT_ROOT / "logs"
            log_dir.mkdir(exist_ok=True)
            out = log_dir / f"tui_session_{ts}.log"
            out.write_text("\n".join(logs), encoding="utf-8")
            self.notify(f"Log saved: {out.name}")
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")

    def action_show_checkpoints(self) -> None:
        self.push_screen(CheckpointsScreen())

    def action_show_artifacts(self) -> None:
        self.push_screen(ArtifactsScreen())

    def action_show_preflight(self) -> None:
        self.push_screen(PreflightScreen())

    async def action_quit(self) -> None:
        if self._pipeline_active:
            def do_quit():
                self.exit()
            self.push_screen(ConfirmModal("A run is active. Quit anyway?", do_quit))
        else:
            self.exit()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        with open(_CRASH_LOG, "w", encoding="utf-8") as f:
            f.write(f"[studio_tui] launched OK — {datetime.now().isoformat()}\n")
    except OSError:
        pass
    try:
        StudioTUI().run()
    except BaseException:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        traceback.print_exc()
        raise
