"""Anti-detection enhancements applied to pages."""

import logging

log = logging.getLogger(__name__)

# JS to mask automation signals not covered by CloakBrowser
STEALTH_JS = """
() => {
    // Hide webdriver property
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    // Fake plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    // Fake languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    // Chrome runtime
    window.chrome = { runtime: {} };
    // Permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
}
"""


def apply_stealth(page):
    """Apply stealth enhancements to a page."""
    page.add_init_script(STEALTH_JS)
    log.debug("Stealth JS injected")


def check_blocked(page) -> str | None:
    """Check if page is blocked. Returns reason string or None."""
    try:
        title = page.title().lower()
        url = page.url.lower()

        # CAPTCHA detection
        if any(x in title for x in ["captcha", "verify", "robot", "unusual traffic"]):
            return f"captcha_detected: {title}"

        # Cloudflare challenge
        if "challenge" in url or "cf-browser-verification" in title:
            return "cloudflare_challenge"

        # Access denied
        if any(x in title for x in ["access denied", "403", "blocked"]):
            return f"access_denied: {title}"

        # Empty page
        body = page.inner_text("body").strip()
        if len(body) < 50:
            return "empty_page"

    except Exception as e:
        log.warning("check_blocked error: %s", e)
    return None
