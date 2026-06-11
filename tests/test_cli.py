import types

import pytest

from discord_scraper import cli
from discord_scraper.api import Unauthorized
from discord_scraper.cli import channel_label, choose_channel


def test_channel_label_for_dm_uses_recipient_display_name():
    ch = {"id": "1", "type": 1,
          "recipients": [{"id": "9", "username": "bob", "global_name": "Bob B"}]}
    assert channel_label(ch) == "Bob B"


def test_channel_label_for_dm_falls_back_to_username():
    ch = {"id": "1", "type": 1,
          "recipients": [{"id": "9", "username": "bob", "global_name": None}]}
    assert channel_label(ch) == "bob"


def test_channel_label_for_group_prefers_name():
    ch = {"id": "2", "type": 3, "name": "The Group",
          "recipients": [{"id": "9", "username": "bob"}]}
    assert channel_label(ch) == "The Group"


def test_channel_label_for_unnamed_group_joins_recipients():
    ch = {"id": "2", "type": 3, "name": None,
          "recipients": [{"username": "bob", "global_name": "Bob"},
                         {"username": "amy", "global_name": "Amy"}]}
    assert channel_label(ch) == "Bob, Amy"


def test_choose_channel_returns_selected(monkeypatch):
    channels = [{"id": "1", "type": 1, "recipients": [{"username": "a"}]},
                {"id": "2", "type": 1, "recipients": [{"username": "b"}]}]
    # Simulate the user typing "2".
    chosen = choose_channel(channels, input_fn=lambda prompt: "2")
    assert chosen["id"] == "2"


# --- Command-dispatch layer (discord-chat-scraper-de5) ---------------------

class _FakeClient:
    """Stand-in for DiscordClient: context manager + list_dm_channels + close."""

    def __init__(self, channels=None, *, raise_unauth=False):
        self._channels = channels or []
        self._raise_unauth = raise_unauth
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def list_dm_channels(self):
        if self._raise_unauth:
            raise Unauthorized()
        return self._channels


def test_main_dispatches_to_named_command(monkeypatch):
    received = {}
    monkeypatch.setitem(cli._COMMANDS, "list",
                        lambda args: received.__setitem__("args", args))
    cli.main(["list"])
    assert received["args"].command == "list"


def test_main_parses_global_db_and_sync_channel(monkeypatch):
    received = {}
    monkeypatch.setitem(cli._COMMANDS, "sync",
                        lambda args: received.__setitem__("args", args))
    cli.main(["--db", "X.db", "sync", "--channel", "9"])
    a = received["args"]
    assert a.command == "sync"
    assert a.db == "X.db"
    assert a.channel == "9"


def test_cmd_auth_forces_relogin_and_reports(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "get_token",
                        lambda *, force_relogin=False: calls.append(force_relogin))
    cli.cmd_auth(types.SimpleNamespace())
    assert calls == [True]
    assert "Token captured and stored." in capsys.readouterr().out


def test_cmd_list_prints_id_and_label(monkeypatch, capsys):
    chans = [
        {"id": "1", "type": 1,
         "recipients": [{"username": "bob", "global_name": "Bob B"}]},
        {"id": "2", "type": 3, "name": "Group", "recipients": []},
    ]
    monkeypatch.setattr(cli, "get_token", lambda *, force_relogin=False: "T")
    monkeypatch.setattr(cli, "DiscordClient", lambda token: _FakeClient(chans))
    cli.cmd_list(types.SimpleNamespace())
    out = capsys.readouterr().out
    assert "1\tBob B" in out
    assert "2\tGroup" in out


def test_cmd_sync_with_channel_id_runs_syncer(monkeypatch, tmp_path, capsys):
    chans = [{"id": "5", "type": 1,
              "recipients": [{"username": "amy", "global_name": "Amy"}]}]
    monkeypatch.setattr(cli, "get_token", lambda *, force_relogin=False: "T")
    monkeypatch.setattr(cli, "DiscordClient", lambda token: _FakeClient(chans))

    captured = {}

    class FakeSyncer:
        def __init__(self, client, db):
            captured["client"] = client
        def sync_channel(self, channel):
            captured["channel"] = channel
            return 7

    monkeypatch.setattr(cli, "Syncer", FakeSyncer)
    args = types.SimpleNamespace(db=str(tmp_path / "a.db"), channel="5")
    cli.cmd_sync(args)

    assert captured["channel"]["id"] == "5"
    assert "Synced 7 message(s)" in capsys.readouterr().out


def test_cmd_sync_reauths_on_unauthorized(monkeypatch, tmp_path):
    chans = [{"id": "5", "type": 1, "recipients": [{"username": "amy"}]}]
    token_calls = []

    def fake_get_token(*, force_relogin=False):
        token_calls.append(force_relogin)
        return "T"

    monkeypatch.setattr(cli, "get_token", fake_get_token)
    clients = iter([_FakeClient(raise_unauth=True), _FakeClient(chans)])
    monkeypatch.setattr(cli, "DiscordClient", lambda token: next(clients))

    captured = {}

    class FakeSyncer:
        def __init__(self, client, db):
            captured["client"] = client
        def sync_channel(self, channel):
            captured["channel"] = channel
            return 1

    monkeypatch.setattr(cli, "Syncer", FakeSyncer)
    args = types.SimpleNamespace(db=str(tmp_path / "a.db"), channel="5")
    cli.cmd_sync(args)

    # First attempt used the stored token, then a forced re-login after 401.
    assert token_calls == [False, True]
    assert captured["channel"]["id"] == "5"


def test_run_sync_with_no_channels_prints_and_skips_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "Database",
        lambda *a, **k: pytest.fail("Database must not be opened when there are no channels"),
    )
    args = types.SimpleNamespace(db=str(tmp_path / "a.db"), channel=None)
    cli._run_sync(_FakeClient([]), [], args)
    assert "No DM or group channels found." in capsys.readouterr().out
