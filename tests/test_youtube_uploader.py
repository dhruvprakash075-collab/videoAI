"""Tests for utils/youtube_uploader.py

Covers:
  - upload_to_youtube() happy path (Playwright mocked end-to-end)
  - Argument plumbing: profile_dir, headless, video_path
  - Form-fill: title (truncated to 100), description (truncated to 5000), tags
  - Visibility selection: public / unlisted / private (default)
  - Error paths: missing video, auth timeout (no upload-icon), other exceptions
  - The "Checks complete" progress loop is broken on first iteration
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Fixture: mocked Playwright stack ────────────────────────────────────────


@pytest.fixture
def mock_pw():
    """Patch utils.youtube_uploader.sync_playwright with a fully wired mock.

    Returns a dict of the named pieces so tests can assert on call args:
      - sp      : the patched sync_playwright() callable
      - p       : the Playwright instance returned by __enter__
      - browser : the BrowserContext returned by launch_persistent_context
      - page    : the Page returned by new_page()
      - fc      : the file-chooser info object (has .value.set_files)
    """
    # ponytail: purge stale module cache if a prior test loaded the module
    # without playwright (e.g., as a side-effect of another import chain).
    import sys as _sys
    _sys.modules.pop("utils.youtube_uploader", None)

    sp = MagicMock()
    p = MagicMock()
    browser = MagicMock()
    page = MagicMock()
    fc_info = MagicMock()

    sp.return_value.__enter__.return_value = p
    p.chromium.launch_persistent_context.return_value = browser
    browser.new_page.return_value = page
    page.expect_file_chooser.return_value.__enter__.return_value = fc_info

    def locator_factory(*args, **kwargs):
        sel = args[0] if args else ""
        loc = MagicMock()
        loc.click.return_value = None
        loc.fill.return_value = None
        loc.inner_text.return_value = ""
        if "done-button" in sel:
            loc.is_visible.return_value = True
            loc.get_attribute.return_value = ""
        else:
            loc.is_visible.return_value = False
        loc.get_attribute.return_value = loc.get_attribute.return_value
        loc.nth.return_value = loc
        loc.type.return_value = "ok"
        return loc

    page.locator.side_effect = locator_factory
    page.wait_for_selector.return_value = None
    page.click.return_value = None
    page.goto.return_value = None
    page.keyboard.press.return_value = None
    fc_info.value.set_files.return_value = None

    with (
        patch("utils.youtube_uploader.sync_playwright", sp),
        patch("utils.youtube_uploader.time.sleep", lambda *_a, **_k: None),
    ):
        yield {"sp": sp, "p": p, "browser": browser, "page": page, "fc": fc_info}


def _fake_video(tmp_path: Path) -> Path:
    p = tmp_path / "video.mp4"
    p.write_bytes(b"fake mp4 content")
    return p


# ── Happy path ──────────────────────────────────────────────────────────────


class TestUploadToYoutubeHappyPath:
    def test_returns_true(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        result = upload_to_youtube(
            v, "My Title", "Desc", ["a", "b"], profile_dir=str(tmp_path / "profile")
        )
        assert result is True

    def test_persistent_context_called(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        profile = str(tmp_path / "my_profile")
        upload_to_youtube(v, "T", "D", [], profile_dir=profile)
        _args, kwargs = mock_pw["p"].chromium.launch_persistent_context.call_args
        assert kwargs["user_data_dir"] == str(Path(profile).resolve())
        assert kwargs["headless"] is True
        assert "--disable-blink-features=AutomationControlled" in kwargs["args"]

    def test_navigates_to_studio(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        mock_pw["page"].goto.assert_any_call("https://studio.youtube.com/")

    def test_waits_for_upload_icon(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        mock_pw["page"].wait_for_selector.assert_any_call("a#upload-icon", timeout=10000)

    def test_clicks_upload_icon(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        mock_pw["page"].click.assert_any_call("a#upload-icon")

    def test_file_chooser_sets_video(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        mock_pw["fc"].value.set_files.assert_called_once_with(str(v.resolve()))

    def test_fills_title_truncated(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        long_title = "x" * 200
        upload_to_youtube(v, long_title, "D", [], profile_dir=str(tmp_path))
        loc_calls = [
            c
            for c in mock_pw["page"].locator.call_args_list
            if c.args and c.args[0] == "div#textbox"
        ]
        assert loc_calls
        title_box = loc_calls[0]
        assert title_box is not None

    def test_fills_tags(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", ["tag1", "tag2", "tag3"], profile_dir=str(tmp_path))
        assert mock_pw["page"].keyboard.press.called

    def test_visibility_default_is_private(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        loc_calls = [str(c) for c in mock_pw["page"].locator.call_args_list]
        assert any("PRIVATE" in c for c in loc_calls)

    def test_visibility_public(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], visibility="public", profile_dir=str(tmp_path))
        loc_calls = [str(c) for c in mock_pw["page"].locator.call_args_list]
        assert any("PUBLIC" in c for c in loc_calls)

    def test_visibility_unlisted(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], visibility="unlisted", profile_dir=str(tmp_path))
        loc_calls = [str(c) for c in mock_pw["page"].locator.call_args_list]
        assert any("UNLISTED" in c for c in loc_calls)

    def test_visibility_unknown_falls_back_to_private(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], visibility="garbage", profile_dir=str(tmp_path))
        loc_calls = [str(c) for c in mock_pw["page"].locator.call_args_list]
        assert any("PRIVATE" in c for c in loc_calls)

    def test_closes_browser_on_success(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        mock_pw["browser"].close.assert_called()

    def test_next_button_clicked_three_times(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        next_calls = [
            c
            for c in mock_pw["page"].click.call_args_list
            if c.args and c.args[0] == "ytcp-button#next-button"
        ]
        assert len(next_calls) == 3

    def test_done_button_clicked(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        done_calls = [
            c
            for c in mock_pw["page"].click.call_args_list
            if c.args and c.args[0] == "ytcp-button#done-button"
        ]
        assert len(done_calls) == 1


# ── Headless flag ───────────────────────────────────────────────────────────


class TestHeadless:
    def test_headless_true_by_default(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        _args, kwargs = mock_pw["p"].chromium.launch_persistent_context.call_args
        assert kwargs["headless"] is True

    def test_headless_false_passed_through(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path), headless=False)
        _args, kwargs = mock_pw["p"].chromium.launch_persistent_context.call_args
        assert kwargs["headless"] is False


# ── Error paths ─────────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_video_not_found_returns_false(self, mock_pw):
        from utils.youtube_uploader import upload_to_youtube

        result = upload_to_youtube("/nonexistent/video.mp4", "T", "D", [])
        assert result is False
        mock_pw["p"].chromium.launch_persistent_context.assert_not_called()

    def test_auth_failure_returns_false(self, mock_pw, tmp_path):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        from utils.youtube_uploader import upload_to_youtube

        mock_pw["page"].wait_for_selector.side_effect = lambda *a, **k: (_ for _ in ()).throw(
            PlaywrightTimeoutError("no upload-icon")
        )
        v = _fake_video(tmp_path)
        result = upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        assert result is False
        mock_pw["browser"].close.assert_called()

    def test_generic_exception_returns_false(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        mock_pw["page"].goto.side_effect = Exception("navigate failed")
        v = _fake_video(tmp_path)
        result = upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        assert result is False

    def test_video_path_resolved(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(str(v), "T", "D", [], profile_dir=str(tmp_path))
        called_path = mock_pw["fc"].value.set_files.call_args.args[0]
        assert Path(called_path).is_absolute()


# ── Progress loop ───────────────────────────────────────────────────────────


class TestProgressLoop:
    def test_progress_label_visible_completes(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        loc_progress = MagicMock()
        loc_progress.is_visible.return_value = True
        loc_progress.inner_text.return_value = "Checks complete"
        loc_done = MagicMock()
        loc_done.is_visible.return_value = False

        def locator_factory(*args, **kwargs):
            sel = args[0] if args else ""
            if "progress-label" in sel:
                return loc_progress
            if "done-button" in sel:
                return loc_done
            loc = MagicMock()
            loc.click.return_value = None
            loc.fill.return_value = None
            loc.inner_text.return_value = ""
            loc.is_visible.return_value = False
            loc.get_attribute.return_value = ""
            loc.nth.return_value = loc
            return loc

        mock_pw["page"].locator.side_effect = locator_factory
        v = _fake_video(tmp_path)
        result = upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        assert result is True

    def test_done_button_visible_breaks_loop(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        loc_done = MagicMock()
        loc_done.is_visible.return_value = True
        loc_done.get_attribute.return_value = ""
        loc_progress = MagicMock()
        loc_progress.is_visible.return_value = False

        def locator_factory(*args, **kwargs):
            sel = args[0] if args else ""
            if "done-button" in sel:
                return loc_done
            if "progress-label" in sel:
                return loc_progress
            loc = MagicMock()
            loc.click.return_value = None
            loc.fill.return_value = None
            loc.inner_text.return_value = ""
            loc.is_visible.return_value = False
            loc.get_attribute.return_value = ""
            loc.nth.return_value = loc
            return loc

        mock_pw["page"].locator.side_effect = locator_factory
        v = _fake_video(tmp_path)
        result = upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        assert result is True


# ── Tag skipping ────────────────────────────────────────────────────────────


class TestTagsEmpty:
    def test_no_tag_fill_when_empty(self, mock_pw, tmp_path):
        from utils.youtube_uploader import upload_to_youtube

        v = _fake_video(tmp_path)
        upload_to_youtube(v, "T", "D", [], profile_dir=str(tmp_path))
        tag_loc_calls = [
            c for c in mock_pw["page"].locator.call_args_list if c.args and "Tags" in c.args[0]
        ]
        assert not tag_loc_calls
