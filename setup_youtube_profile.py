"""setup_youtube_profile.py - Helper to log into YouTube Studio and save the session."""

import argparse
import contextlib
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(
        description="Log into YouTube Studio to save authentication state."
    )
    parser.add_argument(
        "--profile-dir",
        type=str,
        default="chrome_profile",
        help="Directory to save the profile in.",
    )
    args = parser.parse_args()

    profile_path = Path(args.profile_dir).resolve()

    print(f"Opening browser using profile directory: {profile_path}")
    print("Please log into your Google/YouTube account.")
    print("Once you are on the YouTube Studio dashboard, close the browser window.")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.new_page()
        page.goto("https://studio.youtube.com/")

        # Wait until the user closes the browser
        with contextlib.suppress(Exception):
            page.wait_for_timeout(0)  # Wait indefinitely until closed

    print("\nSession saved! The Video.AI pipeline can now auto-upload to this account.")


if __name__ == "__main__":
    main()
