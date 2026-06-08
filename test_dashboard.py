import asyncio

from playwright.async_api import async_playwright


async def test_dashboard():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=1000)
        page = await browser.new_page()

        print("Navigating to dashboard...")
        await page.goto("http://localhost:5173/")

        await page.wait_for_timeout(2000)

        print("Clicking Voice Studio tab...")
        await page.click('button:has-text("Voice Studio")')
        await page.wait_for_timeout(1500)

        print("Clicking A/B Testing tab...")
        await page.click('button:has-text("A/B Testing")')
        await page.wait_for_timeout(1500)

        print("Clicking Director Canvas tab...")
        await page.click('button:has-text("Director Canvas")')
        await page.wait_for_timeout(1500)

        print("Clicking Settings button...")
        await page.click('button:has-text("Settings")')
        await page.wait_for_timeout(2000)

        print("Testing complete! Browser will stay open for 5 seconds...")
        await page.wait_for_timeout(5000)

        await browser.close()
        print("Done!")

if __name__ == "__main__":
    asyncio.run(test_dashboard())
