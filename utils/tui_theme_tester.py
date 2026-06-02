#!/usr/bin/env python3
"""
tui_theme_tester.py - Creative TUI Theme & Animation Playground.

An interactive, premium Textual application designed to explore custom color palettes,
micro-animations, responsive layout grids, and interactive terminal widgets.
Acts as a design reference and playground for studio_tui.py.

Run with:
    venv\\Scripts\\python.exe utils\\tui_theme_tester.py
"""

import contextlib
import random
from datetime import datetime

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.containers import Grid, Horizontal, Vertical
    from textual.theme import Theme
    from textual.widgets import Button, Footer, Header, Label, ProgressBar, Sparkline, Static
except ImportError as e:
    raise ImportError(
        "Textual not installed. Run: venv\\Scripts\\python.exe -m pip install textual"
    ) from e

# ── Custom Creative Neon TCSS Styling ─────────────────────────────────────────
_CSS = """
Screen {
    background: #0d0e15;
}

Header {
    background: #161622;
    color: #82a1ff;
    height: 1;
}

Footer {
    background: #161622;
    color: #4b526d;
    height: 1;
}

#title_panel {
    background: #1a1b26;
    border: double #c0caf5;
    margin: 1 2;
    height: 3;
    content-align: center middle;
    color: #ff9e64;
    text-style: bold;
}

#dashboard_grid {
    grid-size: 2 2;
    grid-gutter: 2 4;
    margin: 1 2;
    height: 1fr;
}

.panel_card {
    background: #1a1b26;
    border: solid #2f3549;
    padding: 1 2;
}

.panel_card:hover {
    border: solid #7aa2f7;
    background: #1f2335;
}

.panel_title {
    color: #bb9afc;
    text-style: bold;
    margin-bottom: 1;
}

#btn_row {
    height: 3;
    margin: 0 2;
    content-align: center middle;
}

Button {
    background: #24283b;
    color: #c0caf5;
    border: none;
    margin-right: 2;
}

Button:hover {
    background: #7aa2f7;
    color: #1a1b26;
}

#btn_tokyo    { border: solid #7aa2f7; }
#btn_cyber    { border: solid #f7768e; }
#btn_dracula  { border: solid #bb9afc; }

/* Custom dynamic loading bar styling */
ProgressBarBar {
    background: #1f2335;
    color: #73daca;
}
"""

# ── Main App ──────────────────────────────────────────────────────────────────

class TUIThemeTester(App):
    """Interactive TUI design sandbox demonstrating high-quality visual aesthetics."""

    CSS = _CSS
    TITLE = "🎬 Video.AI Creative TUI Sandbox"

    def __init__(self):
        super().__init__()
        self.sparkline_data = [random.randint(10, 90) for _ in range(30)]
        self.progress_value = 0.0
        self.current_theme = "Tokyo Night"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("🎬 VIDEO.AI CREATIVE THEME & ANIMATION PLAYGROUND", id="title_panel")

        with Grid(id="dashboard_grid"):
            # 1. Color Palette Inspector Panel
            with Vertical(classes="panel_card"):
                yield Label("🎨 THEME PALETTE DESIGN", classes="panel_title")
                yield Static(id="palette_info")

            # 2. Audio Level / Pacing Micro-Animations Panel
            with Vertical(classes="panel_card"):
                yield Label("📊 REAL-TIME PACING & DYNAMICS SPARKLINE", classes="panel_title")
                yield Sparkline(self.sparkline_data, id="stats_sparkline")
                yield Static("\n[dim]Demonstrates dynamic, non-blocking UI polling and micro-graphs.[/]")

            # 3. Dynamic Progress / VRAM Safety Bar Panel
            with Vertical(classes="panel_card"):
                yield Label("⚡ ENCODING & VRAM DURATION CONTROLS", classes="panel_title")
                yield ProgressBar(total=100, show_eta=True, id="demo_bar")
                yield Static("\n[dim]Simulates dynamic ffmpeg rendering progress bars.[/]")

            # 4. Interactive Command logs Panel
            with Vertical(classes="panel_card"):
                yield Label("📝 INTERACTIVE LOGS & STYLING", classes="panel_title")
                yield Static(id="log_demo_panel")

        with Horizontal(id="btn_row"):
            yield Button("Tokyo Night (Default)", id="btn_tokyo")
            yield Button("Cyberpunk Neon", id="btn_cyber")
            yield Button("Sleek Dracula", id="btn_dracula")

        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.3, self._animate_elements)
        self._update_palette_view()
        self._update_log_view()

    def _animate_elements(self) -> None:
        """Drive micro-animations and status updates."""
        # 1. Animate Sparkline (simulates real-time waveform dynamic range checks)
        self.sparkline_data.pop(0)
        self.sparkline_data.append(random.randint(10, 90))
        with contextlib.suppress(Exception):
            self.query_one("#stats_sparkline", Sparkline).data = self.sparkline_data

        # 2. Animate ProgressBar (simulates segment compilation)
        self.progress_value = (self.progress_value + 1.5) % 100
        with contextlib.suppress(Exception):
            self.query_one("#demo_bar", ProgressBar).progress = self.progress_value

    def _update_palette_view(self) -> None:
        """Render color token indicators dynamically based on selected theme."""
        if self.current_theme == "Tokyo Night":
            info = (
                "[bold #7aa2f7]Tokyo Night Palette[/]\n\n"
                "  - [color=#7aa2f7]■[/] Primary (Neon Blue): #7aa2f7\n"
                "  - [color=#ff9e64]■[/] Accent (Orange):    #ff9e64\n"
                "  - [color=#73daca]■[/] Success (Mint):      #73daca\n"
                "  - [color=#f7768e]■[/] Alert (Red):        #f7768e\n"
                "  - [color=#161622]■[/] Surface (Deep Dark): #161622\n"
            )
        elif self.current_theme == "Cyberpunk":
            info = (
                "[bold #f7768e]Cyberpunk Neon Palette[/]\n\n"
                "  - [color=#f7768e]■[/] Primary (Hot Pink):   #f7768e\n"
                "  - [color=#ffeb3b]■[/] Accent (Yellow):     #ffeb3b\n"
                "  - [color=#00e676]■[/] Success (Neon Green): #00e676\n"
                "  - [color=#00e5ff]■[/] Info (Cyan):         #00e5ff\n"
                "  - [color=#0b0b12]■[/] Background (Pitch):  #0b0b12\n"
            )
        else:  # Dracula
            info = (
                "[bold #bb9afc]Sleek Dracula Palette[/]\n\n"
                "  - [color=#bb9afc]■[/] Primary (Purple):     #bb9afc\n"
                "  - [color=#ff79c6]■[/] Accent (Pink):       #ff79c6\n"
                "  - [color=#50fa7b]■[/] Success (Lime):       #50fa7b\n"
                "  - [color=#ff5555]■[/] Alert (Red):        #ff5555\n"
                "  - [color=#1a1a24]■[/] Surface (Slate):      #1a1a24\n"
            )
        with contextlib.suppress(Exception):
            self.query_one("#palette_info", Static).update(info)

    def _update_log_view(self) -> None:
        """Render beautifully formatted log message examples."""
        t = datetime.now().strftime("%H:%M:%S")
        logs = (
            f"[dim]{t}[/] [bold #4a90d9]INFO[/]  [#7aa2f7]Starting Video.AI local TUI rendering engine...[/]\n"
            f"[dim]{t}[/] [bold #5a9e6f]SUCCESS[/] [#73daca]Ollama server connection initialized. Resident: zephyr-writer[/]\n"
            f"[dim]{t}[/] [bold #ff9e64]WARN[/]    [#ff9e64]VRAM preflight checks: 6GB RTX 4050 detected.[/]\n"
            f"[dim]{t}[/] [bold #e05252]ERROR[/]   [#f7768e]WSL communication timeout. Falling back to classic assembler.[/]"
        )
        with contextlib.suppress(Exception):
            self.query_one("#log_demo_panel", Static).update(logs)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle theme switching via interactive buttons."""
        if event.button.id == "btn_tokyo":
            self.current_theme = "Tokyo Night"
            self.notify("Theme switched to: Tokyo Night", severity="information")
        elif event.button.id == "btn_cyber":
            self.current_theme = "Cyberpunk"
            self.notify("Theme switched to: Cyberpunk Neon", severity="warning")
        elif event.button.id == "btn_dracula":
            self.current_theme = "Dracula"
            self.notify("Theme switched to: Sleek Dracula", severity="information")
        self._update_palette_view()

if __name__ == "__main__":
    app = TUIThemeTester()
    app.run()
