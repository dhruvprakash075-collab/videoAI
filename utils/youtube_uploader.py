"""youtube_uploader.py - Automates video uploads to YouTube Studio via Playwright."""

import contextlib
import logging
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

log = logging.getLogger(__name__)


def upload_to_youtube(
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str],
    visibility: str = "private",
    profile_dir: str | Path = "chrome_profile",
    headless: bool = True,
) -> bool:
    """Upload a video to YouTube using a persistent Chrome profile.

    Args:
        video_path: Path to the MP4 file.
        title: Video title (max 100 chars).
        description: Video description.
        tags: List of tags.
        visibility: "private", "unlisted", or "public".
        profile_dir: Directory containing the authenticated Chrome profile.
        headless: Run browser in headless mode (set to False for initial login).

    Returns:
        True if upload succeeds, False otherwise.
    """
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        log.error(f"[YouTube] Video not found: {video_path}")
        return False

    profile_dir = Path(profile_dir).resolve()
    log.info(f"[YouTube] Starting upload for '{title}' (visibility: {visibility})")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = browser.new_page()

            log.debug("[YouTube] Navigating to studio.youtube.com...")
            page.goto("https://studio.youtube.com/")

            try:
                page.wait_for_selector("a#upload-icon", timeout=10000)
            except PlaywrightTimeoutError:
                log.error(
                    "[YouTube] Authentication failed! Please run setup_youtube_profile.py to log in."
                )
                browser.close()
                return False

            log.debug("[YouTube] Clicking upload icon...")
            page.click("a#upload-icon")

            log.debug("[YouTube] Setting file input...")
            with page.expect_file_chooser() as fc_info:
                page.click("ytcp-button#select-files-button")
            file_chooser = fc_info.value
            file_chooser.set_files(str(video_path))

            log.debug("[YouTube] Waiting for upload modal...")
            page.wait_for_selector("ytcp-video-metadata-editor", timeout=60000)

            log.debug("[YouTube] Filling title...")
            title_box = page.locator("div#textbox").nth(0)
            title_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            title_box.fill(title[:100])

            log.debug("[YouTube] Filling description...")
            desc_box = page.locator("div#textbox").nth(1)
            desc_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            desc_box.fill(description[:5000])

            log.debug("[YouTube] Selecting 'Not made for kids'...")
            page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").click()

            log.debug("[YouTube] Expanding advanced settings...")
            with contextlib.suppress(PlaywrightTimeoutError):
                page.click("ytcp-button#toggle-button", timeout=5000)

            if tags:
                log.debug("[YouTube] Filling tags...")
                tags_str = ", ".join(tags)
                tag_box = page.locator("input[aria-label='Tags']")
                tag_box.click()
                tag_box.fill(tags_str)
                page.keyboard.press("Enter")

            log.debug("[YouTube] Proceeding to Visibility tab...")
            for _ in range(3):
                page.click("ytcp-button#next-button")
                time.sleep(1)

            log.debug(f"[YouTube] Setting visibility to '{visibility}'...")
            vis_lower = visibility.lower()
            if vis_lower == "public":
                page.locator("tp-yt-paper-radio-button[name='PUBLIC']").click()
            elif vis_lower == "unlisted":
                page.locator("tp-yt-paper-radio-button[name='UNLISTED']").click()
            else:
                page.locator("tp-yt-paper-radio-button[name='PRIVATE']").click()

            log.info(
                "[YouTube] Waiting for video processing checks to complete... (this may take a few minutes)"
            )
            while True:
                progress = (
                    page.locator("span.progress-label").inner_text()
                    if page.locator("span.progress-label").is_visible()
                    else ""
                )
                if (
                    "Checks complete" in progress
                    or "Upload complete" in progress
                    or "Processing up to" in progress
                ):
                    break
                if page.locator(
                    "ytcp-button#done-button"
                ).is_visible() and "disabled" not in page.locator(
                    "ytcp-button#done-button"
                ).get_attribute("class"):
                    break
                time.sleep(5)

            log.debug("[YouTube] Clicking Save/Publish...")
            page.click("ytcp-button#done-button")

            page.wait_for_selector("ytcp-video-share-dialog", timeout=30000)
            video_link = (
                page.locator("a.ytcp-video-info").inner_text()
                if page.locator("a.ytcp-video-info").is_visible()
                else "Unknown"
            )

            log.info(f"[YouTube] Upload complete! Video link: {video_link}")
            browser.close()
            return True

    except Exception as e:
        log.exception(f"[YouTube] Upload failed with error: {e}")
        return False
