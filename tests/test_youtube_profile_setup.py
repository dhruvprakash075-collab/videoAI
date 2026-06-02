"""Tests for setup_youtube_profile.py

Covers:
  - main() default args, custom --profile-dir
  - Launch flow: persistent context, headless=False, navigates to studio
  - Output: profile dir printed, login prompt, "Session saved" message
  - Edge cases: profile dir resolved to absolute path
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# ── main() happy paths ──────────────────────────────────────────────────────

class TestMainDefaults:
    def test_default_profile_dir(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp, \
             redirect_stdout(io.StringIO()) as out:
            main()
        p, _browser, _page = _capture_pw(mock_sp)
        _args, kwargs = p.chromium.launch_persistent_context.call_args
        assert kwargs["user_data_dir"] == str(Path("chrome_profile").resolve())
        assert kwargs["headless"] is False
        assert "Opening browser" in out.getvalue()
        assert "Session saved" in out.getvalue()


class TestMainCustomDir:
    def test_custom_profile_dir(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py", "--profile-dir", "my_dir"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp, \
             redirect_stdout(io.StringIO()) as out:
            main()
        p, _browser, _page = _capture_pw(mock_sp)
        _args, kwargs = p.chromium.launch_persistent_context.call_args
        assert kwargs["user_data_dir"] == str(Path("my_dir").resolve())
        assert "my_dir" in out.getvalue()

    def test_absolute_profile_dir_kept(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py", "--profile-dir", "/tmp/p"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp:
            main()
        p, _browser, _page = _capture_pw(mock_sp)
        _args, kwargs = p.chromium.launch_persistent_context.call_args
        assert Path(kwargs["user_data_dir"]).is_absolute()


# ── Browser launch behavior ─────────────────────────────────────────────────

class TestBrowserLaunch:
    def test_uses_persistent_context(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp:
            main()
        p, _b, _page = _capture_pw(mock_sp)
        assert p.chromium.launch_persistent_context.called

    def test_navigates_to_studio(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp:
            main()
        _p, _b, page = _capture_pw(mock_sp)
        page.goto.assert_called_with("https://studio.youtube.com/")

    def test_creates_new_page(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp:
            main()
        _p, browser, _page = _capture_pw(mock_sp)
        browser.new_page.assert_called()

    def test_automation_disabled_arg(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright") as mock_sp:
            main()
        p, _b, _page = _capture_pw(mock_sp)
        _args, kwargs = p.chromium.launch_persistent_context.call_args
        assert "--disable-blink-features=AutomationControlled" in kwargs["args"]


# ── Output messaging ────────────────────────────────────────────────────────

class TestOutput:
    def test_prints_profile_path(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright"), \
             redirect_stdout(io.StringIO()) as out:
            main()
        output = out.getvalue()
        assert "Opening browser" in output
        assert "chrome_profile" in output

    def test_prints_login_prompt(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright"), \
             redirect_stdout(io.StringIO()) as out:
            main()
        output = out.getvalue()
        assert "log into" in output.lower() or "log in" in output.lower()

    def test_prints_session_saved(self):
        from setup_youtube_profile import main
        with patch("sys.argv", ["setup_youtube_profile.py"]), \
             patch("setup_youtube_profile.sync_playwright"), \
             redirect_stdout(io.StringIO()) as out:
            main()
        output = out.getvalue()
        assert "Session saved" in output


# ── Helper ──────────────────────────────────────────────────────────────────

def _capture_pw(mock_sp):
    """Extract (p, browser, page) from a mocked sync_playwright()."""
    p = mock_sp.return_value.__enter__.return_value
    browser = p.chromium.launch_persistent_context.return_value
    page = browser.new_page.return_value
    return p, browser, page
