import contextlib
import time
from playwright.sync_api import sync_playwright, Page, BrowserContext
from autoapply.logging import get_logger

log = get_logger(__name__)

@contextlib.contextmanager
def get_browser_context() -> BrowserContext:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        try:
            yield context
        finally:
            context.close()
            browser.close()

def safe_fill(page: Page, selector: str, value: str, timeout: int = 5000) -> bool:
    if not value:
        return False
    try:
        el = page.locator(selector)
        if el.count() > 0:
            el.first.wait_for(state="visible", timeout=timeout)
            el.first.fill(value)
            return True
    except Exception as e:
        log.debug("safe_fill_failed", selector=selector, error=str(e))
    return False

def check_for_captcha(page: Page) -> bool:
    captcha_selectors = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#cf-turnstile",
        ".g-recaptcha"
    ]
    for sel in captcha_selectors:
        try:
            if page.locator(sel).count() > 0 and page.locator(sel).first.is_visible():
                return True
        except:
            pass
    return False
