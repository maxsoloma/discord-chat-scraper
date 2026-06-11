"""Command-line interface: auth, list, sync (DMs, groups, and servers)."""

import argparse

from .api import DiscordClient, Forbidden, Unauthorized
from .auth import get_token
from .db import Database
from .sync import Syncer

DEFAULT_DB = "discord_archive.db"

# Sentinel returned by selection menus when the user chooses "Back".
BACK = object()

# Guild channel types whose top-level message timeline can be archived.
GUILD_TEXT_TYPES = (0, 5)  # GUILD_TEXT, GUILD_ANNOUNCEMENT


def _recipient_name(user):
    return user.get("global_name") or user.get("username") or "unknown"


def channel_label(channel):
    """Human-readable label for a DM, group-DM, or guild text channel."""
    ctype = channel.get("type")
    if ctype in GUILD_TEXT_TYPES:  # guild text / announcement channel
        return "#" + (channel.get("name") or str(channel.get("id")))
    if ctype == 3:  # GROUP_DM
        if channel.get("name"):
            return channel["name"]
        names = [_recipient_name(u) for u in channel.get("recipients") or []]
        return ", ".join(names) if names else "(empty group)"
    recipients = channel.get("recipients") or []
    if recipients:
        return _recipient_name(recipients[0])
    return f"DM {channel.get('id')}"


def guild_label(guild):
    return guild.get("name") or str(guild.get("id"))


def choose(items, label_fn, prompt, *, input_fn=input):
    """Numbered selection menu with a Back option. Returns the item or BACK."""
    for i, item in enumerate(items, start=1):
        print(f"  {i}) {label_fn(item)}")
    print("  0) Back")
    while True:
        raw = input_fn(f"{prompt} [0-{len(items)}]: ").strip()
        if raw == "0":
            return BACK
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("Invalid selection, try again.")


def choose_channel(channels, *, input_fn=input):
    """Numbered chat menu; returns the chosen channel or BACK."""
    return choose(channels, channel_label, "Select a chat", input_fn=input_fn)


def _pick_action(title, choices, *, input_fn=input):
    """Show a titled action menu; return the 1-based choice, or 0 for Back."""
    print(f"\n{title}")
    for i, label in enumerate(choices, start=1):
        print(f"  {i}) {label}")
    print("  0) Back")
    while True:
        raw = input_fn(f"Select [0-{len(choices)}]: ").strip()
        if raw == "0":
            return 0
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return int(raw)
        print("Invalid selection, try again.")


def _client(force_relogin=False):
    return DiscordClient(get_token(force_relogin=force_relogin))


def cmd_auth(args):
    get_token(force_relogin=True)
    print("Token captured and stored.")


def cmd_list(args):
    with _client() as client:
        for ch in client.list_dm_channels():
            print(f"{ch['id']}\t{channel_label(ch)}")


def cmd_sync(args):
    """Run a sync session, transparently re-authenticating once on a stale token."""
    try:
        _sync_session(_client(), args)
    except Unauthorized:
        _sync_session(_client(force_relogin=True), args)


def _sync_session(client, args):
    with client:
        db = Database(args.db)
        try:
            _dispatch_sync(client, db, args)
        finally:
            db.close()


def _dispatch_sync(client, db, args):
    if args.channel:
        _sync_one_channel(client, db, {"id": args.channel}, args)
    elif args.guild:
        _sync_whole_guild(client, db, {"id": args.guild}, args)
    elif args.dms:
        _dm_flow(client, db, args)
    elif args.server:
        _server_flow(client, db, args)
    else:
        _source_menu(client, db, args)


def _source_menu(client, db, args, *, input_fn=input):
    while True:
        choice = _pick_action(
            "Sync from:",
            ["Direct messages / groups", "A server"],
            input_fn=input_fn,
        )
        if choice == 0:
            return
        if choice == 1:
            _dm_flow(client, db, args, input_fn=input_fn)
        else:
            _server_flow(client, db, args, input_fn=input_fn)


def _dm_flow(client, db, args, *, input_fn=input):
    channels = client.list_dm_channels()
    if not channels:
        print("No DM or group channels found.")
        return
    while True:
        target = choose(channels, channel_label, "Select a chat", input_fn=input_fn)
        if target is BACK:
            return
        _sync_one_channel(client, db, target, args)


def _server_flow(client, db, args, *, input_fn=input):
    guilds = client.list_guilds()
    if not guilds:
        print("No servers found.")
        return
    while True:
        guild = choose(guilds, guild_label, "Select a server", input_fn=input_fn)
        if guild is BACK:
            return
        _guild_scope_menu(client, db, guild, args, input_fn=input_fn)


def _guild_scope_menu(client, db, guild, args, *, input_fn=input):
    while True:
        choice = _pick_action(
            f"Server: {guild_label(guild)}",
            ["A specific channel", "The whole server (all text channels)"],
            input_fn=input_fn,
        )
        if choice == 0:
            return
        if choice == 1:
            _guild_channel_flow(client, db, guild, args, input_fn=input_fn)
        else:
            _sync_whole_guild(client, db, guild, args)


def _guild_channel_flow(client, db, guild, args, *, input_fn=input):
    channels = client.list_guild_channels(guild["id"])
    if not channels:
        print("No text channels found in this server.")
        return
    while True:
        target = choose(channels, channel_label, "Select a channel", input_fn=input_fn)
        if target is BACK:
            return
        _sync_one_channel(client, db, target, args)


def _sync_whole_guild(client, db, guild, args):
    channels = client.list_guild_channels(guild["id"])
    if not channels:
        print("No text channels found in this server.")
        return
    print(f"Syncing {len(channels)} text channel(s) from {guild_label(guild)}...")
    for channel in channels:
        _sync_one_channel(client, db, channel, args)


def _sync_one_channel(client, db, channel, args):
    label = channel_label(channel)
    try:
        count = Syncer(client, db).sync_channel(channel)
    except Forbidden:
        print(f"  skipped {label} (no access)")
        return
    print(f"Synced {count} message(s) for {label} -> {args.db}")


def build_parser():
    parser = argparse.ArgumentParser(prog="discord-scraper")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="log in via browser and store the token")
    sub.add_parser("list", help="list your DM and group channels")
    p_sync = sub.add_parser("sync", help="fetch/update channels into the database")
    p_sync.add_argument("--channel", help="sync just this channel id")
    p_sync.add_argument("--guild", help="sync every text channel of this server id")
    p_sync.add_argument(
        "--dms", action="store_true", help="go straight to the DM / group list"
    )
    p_sync.add_argument(
        "--server", action="store_true", help="go straight to the server flow"
    )

    return parser


_COMMANDS = {"auth": cmd_auth, "list": cmd_list, "sync": cmd_sync}


def main(argv=None):
    args = build_parser().parse_args(argv)
    _COMMANDS[args.command](args)
