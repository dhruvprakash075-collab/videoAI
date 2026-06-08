from playwright.sync_api import sync_playwright
import time

def test_dashboard():
    print("Starting browser test...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page()
        
        print("Opening dashboard...")
        page.goto("http://localhost:5173/")
        time.sleep(2)
        
        print("Testing tab navigation...")
        print("Clicking Voice Studio...")
        page.click('button:has-text("Voice Studio")')
        time.sleep(1)
        
        print("Clicking A/B Testing...")
        page.click('button:has-text("A/B Testing")')
        time.sleep(1)
        
        print("Clicking Director Canvas...")
        page.click('button:has-text("Director Canvas")')
        time.sleep(1)
        
        print("Clicking Settings...")
        page.click('button:has-text("Settings")')
        time.sleep(2)
        
        print("Taking screenshot...")
        page.screenshot(path="dashboard_e2e_test.png")
        
        print("Test complete! Keeping browser open for 5 seconds...")
        time.sleep(5)
        
        browser.close()
        print("Done!")

if __name__ == "__main__":
    test_dashboard()
