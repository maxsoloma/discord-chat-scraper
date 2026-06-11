import types

import pytest

from discord_scraper import cli
from discord_scraper.api import Forbidden, Unauthorized
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
    """Stand-in for DiscordClient: context manager + channel/guild listings."""

    def __init__(self, channels=None, *, raise_unauth=False, guilds=None,
                 guild_channels=None):
        self._channels = channels or []
        self._raise_unauth = raise_unauth
        self._guilds = guilds or []
        self._guild_channels = guild_channels or {}  # {guild_id: [channel dicts]}
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

    def list_guilds(self):
        if self._raise_unauth:
            raise Unauthorized()
        return self._guilds

    def list_guild_channels(self, guild_id):
        return self._guild_channels.get(guild_id, [])


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
        def sync_channel(self, channel, on_progress=None):
            captured["channel"] = channel
            return 7

    monkeypatch.setattr(cli, "Syncer", FakeSyncer)
    args = types.SimpleNamespace(db=str(tmp_path / "a.db"), channel="5")
    cli.cmd_sync(args)

    assert captured["channel"]["id"] == "5"
    assert "Synced 7 message(s)" in capsys.readouterr().out


def test_cmd_sync_reauths_on_unauthorized(monkeypatch, tmp_path):
    token_calls = []

    def fake_get_token(*, force_relogin=False):
        token_calls.append(force_relogin)
        return "T"

    monkeypatch.setattr(cli, "get_token", fake_get_token)
    monkeypatch.setattr(cli, "DiscordClient", lambda token: _FakeClient())

    synced = []

    class FlakySyncer:
        calls = 0

        def __init__(self, client, db):
            pass

        def sync_channel(self, channel, on_progress=None):
            FlakySyncer.calls += 1
            if FlakySyncer.calls == 1:
                raise Unauthorized()  # stale token surfaces during the fetch
            synced.append(channel["id"])
            return 2

    monkeypatch.setattr(cli, "Syncer", FlakySyncer)
    args = _sync_args(tmp_path, channel="5")
    cli.cmd_sync(args)

    # Stored token first, then a forced re-login after 401; second attempt works.
    assert token_calls == [False, True]
    assert synced == ["5"]


def _sync_args(tmp_path, *, channel=None, guild=None, dms=False, server=False):
    return types.SimpleNamespace(
        db=str(tmp_path / "a.db"), channel=channel, guild=guild, dms=dms, server=server
    )


def _recording_syncer(monkeypatch, *, forbidden_ids=()):
    synced = []

    class RecSyncer:
        def __init__(self, client, db):
            pass

        def sync_channel(self, channel, on_progress=None):
            if channel["id"] in forbidden_ids:
                raise Forbidden(channel["id"])
            synced.append(channel["id"])
            return 1

    monkeypatch.setattr(cli, "Syncer", RecSyncer)
    return synced


# --- Server (guild) support + back navigation ------------------------------

def test_channel_label_for_guild_text_channel_uses_hash_name():
    assert channel_label({"id": "7", "type": 0, "name": "general"}) == "#general"
    assert channel_label({"id": "8", "type": 5, "name": "news"}) == "#news"


def test_guild_label_uses_name_then_id():
    assert cli.guild_label({"id": "9", "name": "My Server"}) == "My Server"
    assert cli.guild_label({"id": "9"}) == "9"


def test_choose_returns_back_on_zero():
    result = cli.choose([{"id": "1"}], lambda c: c["id"], "Pick",
                        input_fn=lambda prompt: "0")
    assert result is cli.BACK


def test_choose_retries_on_invalid_then_returns_item():
    inputs = iter(["x", "9", "2"])  # non-numeric, out-of-range, then valid
    result = cli.choose([{"id": "a"}, {"id": "b"}], lambda c: c["id"], "Pick",
                        input_fn=lambda prompt: next(inputs))
    assert result == {"id": "b"}


def test_dm_flow_with_no_channels_prints_message(capsys):
    cli._dm_flow(_FakeClient([]), None, types.SimpleNamespace(db="x.db"))
    assert "No DM or group channels found." in capsys.readouterr().out


def test_sync_whole_guild_syncs_text_channels_and_skips_forbidden(monkeypatch, capsys):
    synced = _recording_syncer(monkeypatch, forbidden_ids={"c2"})
    client = _FakeClient(guild_channels={"g1": [
        {"id": "c1", "type": 0, "name": "general"},
        {"id": "c2", "type": 0, "name": "secret"},
        {"id": "c3", "type": 5, "name": "news"},
    ]})
    cli._sync_whole_guild(client, None, {"id": "g1", "name": "Srv"},
                          types.SimpleNamespace(db="x.db"))
    assert synced == ["c1", "c3"]  # c2 raised Forbidden -> skipped
    out = capsys.readouterr().out
    assert "Syncing 3 text channel(s) from Srv" in out
    assert "skipped #secret (no access)" in out


def test_server_flow_back_at_guild_list_exits(monkeypatch):
    synced = _recording_syncer(monkeypatch)
    client = _FakeClient(guilds=[{"id": "g1", "name": "Srv"}])
    cli._server_flow(client, None, types.SimpleNamespace(db="x.db"),
                     input_fn=lambda prompt: "0")
    assert synced == []


def test_server_flow_back_from_scope_returns_to_guild_list(monkeypatch):
    # pick guild 1 -> scope menu Back(0) -> back at guild list -> Back(0) exits
    inputs = iter(["1", "0", "0"])
    synced = _recording_syncer(monkeypatch)
    client = _FakeClient(
        guilds=[{"id": "g1", "name": "Srv"}],
        guild_channels={"g1": [{"id": "c1", "type": 0, "name": "general"}]},
    )
    cli._server_flow(client, None, types.SimpleNamespace(db="x.db"),
                     input_fn=lambda prompt: next(inputs))
    assert synced == []  # backed all the way out without syncing


def test_server_flow_whole_server_then_back(monkeypatch):
    # guild 1 -> scope "2" (whole server) -> scope Back(0) -> guild Back(0)
    inputs = iter(["1", "2", "0", "0"])
    synced = _recording_syncer(monkeypatch)
    client = _FakeClient(
        guilds=[{"id": "g1", "name": "Srv"}],
        guild_channels={"g1": [
            {"id": "c1", "type": 0, "name": "general"},
            {"id": "c2", "type": 5, "name": "news"},
        ]},
    )
    cli._server_flow(client, None, types.SimpleNamespace(db="x.db"),
                     input_fn=lambda prompt: next(inputs))
    assert synced == ["c1", "c2"]


def test_source_menu_dm_then_back(monkeypatch):
    # source "1" (DMs) -> chat list Back(0) -> source Back(0)
    inputs = iter(["1", "0", "0"])
    synced = _recording_syncer(monkeypatch)
    client = _FakeClient(channels=[{"id": "d1", "type": 1,
                                    "recipients": [{"username": "a"}]}])
    cli._source_menu(client, None, types.SimpleNamespace(db="x.db"),
                     input_fn=lambda prompt: next(inputs))
    assert synced == []


def test_guild_channel_flow_syncs_picked_channel_then_back(monkeypatch):
    # pick channel 1 -> sync it -> Back(0) returns to the scope menu
    inputs = iter(["1", "0"])
    synced = _recording_syncer(monkeypatch)
    client = _FakeClient(guild_channels={"g1": [{"id": "c1", "type": 0,
                                                 "name": "general"}]})
    cli._guild_channel_flow(client, None, {"id": "g1", "name": "Srv"},
                            types.SimpleNamespace(db="x.db"),
                            input_fn=lambda prompt: next(inputs))
    assert synced == ["c1"]


def test_main_exits_cleanly_on_ctrl_d(monkeypatch):
    def boom(args):
        raise EOFError()

    monkeypatch.setitem(cli._COMMANDS, "list", boom)
    cli.main(["list"])  # must NOT raise — EOFError handled in main


def test_sync_one_channel_wires_progress_callback(monkeypatch, capsys):
    received = {}

    class CapturingSyncer:
        def __init__(self, client, db):
            pass

        def sync_channel(self, channel, on_progress=None):
            received["callback"] = on_progress
            on_progress(42)  # must be safe to call (no-op when not a TTY)
            return 5

    monkeypatch.setattr(cli, "Syncer", CapturingSyncer)
    cli._sync_one_channel(None, None, {"id": "9", "type": 0, "name": "gen"},
                          types.SimpleNamespace(db="x.db"))
    assert callable(received["callback"])
    assert "Synced 5 message(s) for #gen" in capsys.readouterr().out
