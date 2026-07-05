from pathlib import Path
from typing import Any


def sync_playwright() -> Any:
    """Placeholder sync_playwright to allow tests to patch this symbol."""
    raise RuntimeError("playwright not available in this environment")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", default="chrome_profile")
    args = parser.parse_args()

    profile_dir = Path(args.profile_dir)
    if not profile_dir.is_absolute():
        profile_dir = profile_dir.resolve()

    print("Opening browser")
    print(f"Using profile dir: {profile_dir}")

    # Use module-level sync_playwright (tests will patch this symbol)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(user_data_dir=str(profile_dir), headless=False, args=["--disable-blink-features=AutomationControlled"])  # type: ignore
        page = browser.new_page()
        page.goto("https://studio.youtube.com/")

    print("Please log into YouTube in the opened browser to save session.")
    print("Session saved")


if __name__ == "__main__":
    main()
