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


import threading

DISCORD_API_PREFIX = "https://discord.com/api/"
LOGIN_URL = "https://discord.com/login"


def capture_token_via_browser():
    """Open a real Chromium window; return the user token once the human logs in.

    The token is read from the Authorization header of the first authenticated
    request to the Discord API. Requires `playwright install chromium`.
    """
    from playwright.sync_api import sync_playwright

    holder = {"token": None}
    captured = threading.Event()

    def on_request(request):
        if not request.url.startswith(DISCORD_API_PREFIX):
            return
        token = request.header_value("authorization")
        if token:  # skip the tokenless /api/ calls Discord makes before login
            holder["token"] = token
            captured.set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.on("request", on_request)  # context-level: SPA/iframe safe
        page = context.new_page()
        page.goto(LOGIN_URL)
        print("Log in to Discord in the opened window (incl. MFA). Waiting...")
        captured.wait()  # blocks until the header is found (no timeout)
        browser.close()

    return holder["token"]


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
