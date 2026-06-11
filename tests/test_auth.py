import keyring
import pytest
from keyring.errors import NoKeyringError

from discord_scraper import auth


class _MemKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


def test_store_and_load_token(monkeypatch):
    mem = _MemKeyring()
    monkeypatch.setattr(keyring, "set_password", mem.set_password)
    monkeypatch.setattr(keyring, "get_password", mem.get_password)

    auth.store_token("TKN")
    assert auth.load_token() == "TKN"


def test_load_token_returns_none_when_absent(monkeypatch):
    mem = _MemKeyring()
    monkeypatch.setattr(keyring, "get_password", mem.get_password)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    assert auth.load_token() is None


def test_load_token_falls_back_to_env_when_no_backend(monkeypatch):
    def boom(service, user):
        raise NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setenv("DISCORD_TOKEN", "FROM_ENV")
    assert auth.load_token() == "FROM_ENV"


def test_token_capture_pumps_event_loop_and_records_header():
    """Regression for the auth hang.

    The bug: the request listener was registered with `context.on(...)` but the
    code then blocked on `threading.Event().wait()`, which does NOT pump
    Playwright's sync event loop, so the handler never fired and login appeared
    to do nothing. This drives the capture helpers against a real (headless)
    browser whose page issues one authenticated API request; the pump loop must
    dispatch the handler and return the captured token.
    """
    playwright_sync = pytest.importorskip("playwright.sync_api")

    api_prefix = "https://capture.test/api/"
    html = (
        "<script>"
        "fetch('https://capture.test/api/v9/users/@me',"
        "{headers:{authorization:'TESTTOKEN'}}).catch(function(){});"
        "</script>"
    )
    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # browser binary not installed in this env
            pytest.skip(f"Chromium not available: {exc}")
        try:
            context = browser.new_context()
            holder = auth._install_token_capture(context, api_prefix=api_prefix)
            page = context.new_page()
            # Fulfill the request locally so the test needs no network.
            page.route(
                "https://capture.test/**",
                lambda route: route.fulfill(status=200, body="{}"),
            )
            page.set_content(html)
            token = auth._poll_until_captured(page, holder, poll_ms=50, max_polls=100)
        finally:
            browser.close()

    assert token == "TESTTOKEN"
