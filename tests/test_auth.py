import keyring
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
