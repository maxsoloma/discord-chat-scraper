"""Token acquisition: keyring storage with env fallback + browser capture."""

import os

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError

SERVICE = "discord-chat-scraper"
USERNAME = "user-token"
ENV_VAR = "DISCORD_TOKEN"


def store_token(token):
    keyring.set_password(SERVICE, USERNAME, token)


def load_token():
    """Return the stored token, or the DISCORD_TOKEN env var, or None."""
    try:
        token = keyring.get_password(SERVICE, USERNAME)
    except NoKeyringError:
        token = None
    if token:
        return token
    return os.environ.get(ENV_VAR)


def clear_token():
    try:
        keyring.delete_password(SERVICE, USERNAME)
    except (PasswordDeleteError, NoKeyringError):
        pass


DISCORD_API_PREFIX = "https://discord.com/api/"
LOGIN_URL = "https://discord.com/login"


def _install_token_capture(context, *, api_prefix=DISCORD_API_PREFIX):
    """Attach a request listener that records the first authenticated token.

    Returns a mutable holder whose ``token`` key is filled in once an
    authenticated request to the API is seen. Filtering on the presence of the
    ``Authorization`` header skips the tokenless ``/api/`` calls Discord makes
    before login. Attach this on the CONTEXT (not a single page) so requests
    from any page/iframe are observed.
    """
    holder = {"token": None}

    def on_request(request):
        if holder["token"] is None and request.url.startswith(api_prefix):
            token = request.header_value("authorization")
            if token:
                holder["token"] = token

    context.on("request", on_request)
    return holder


def _poll_until_captured(page, holder, *, poll_ms=250, max_polls=None):
    """Block until ``holder['token']`` is set, then return it.

    The wait MUST go through a Playwright call (``page.wait_for_timeout``).
    In Playwright's SYNC API, ``.on('request', ...)`` handlers are dispatched
    only while the main thread is inside a Playwright call. Blocking on a plain
    ``threading.Event().wait()`` never pumps the event loop, so the handler
    never fires and login appears to "do nothing" — that was the bug this
    replaces. ``max_polls`` bounds the loop for tests; production passes
    ``None`` to wait indefinitely for the human to finish logging in.
    """
    polls = 0
    while holder["token"] is None:
        if page.is_closed():
            raise RuntimeError("Browser window was closed before login completed.")
        page.wait_for_timeout(poll_ms)
        polls += 1
        if max_polls is not None and polls >= max_polls:
            break
    return holder["token"]


def capture_token_via_browser():
    """Open a real Chromium window; return the user token once the human logs in.

    The token is read from the Authorization header of the first authenticated
    request to the Discord API. Requires `playwright install chromium`.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            holder = _install_token_capture(context)  # attach BEFORE navigating
            page = context.new_page()
            page.goto(LOGIN_URL)
            print(
                "Log in to Discord in the opened window (including MFA). "
                "It will close automatically once your token is captured."
            )
            return _poll_until_captured(page, holder)
        finally:
            browser.close()


def get_token(*, force_relogin=False):
    """Return a usable token: stored one, or capture a fresh one via the browser."""
    if not force_relogin:
        existing = load_token()
        if existing:
            return existing
    token = capture_token_via_browser()
    if not token:
        raise RuntimeError("Failed to capture a token from the browser session.")
    store_token(token)
    return token
