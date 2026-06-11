"""Command-line interface: auth, list, sync."""

import argparse

from .api import DiscordClient, Unauthorized
from .auth import get_token
from .db import Database
from .sync import Syncer

DEFAULT_DB = "discord_archive.db"


def _recipient_name(user):
    return user.get("global_name") or user.get("username") or "unknown"


def channel_label(channel):
    """Human-readable label for a DM or group-DM channel."""
    if channel.get("type") == 3:  # GROUP_DM
        if channel.get("name"):
            return channel["name"]
        names = [_recipient_name(u) for u in channel.get("recipients") or []]
        return ", ".join(names) if names else "(empty group)"
    recipients = channel.get("recipients") or []
    if recipients:
        return _recipient_name(recipients[0])
    return f"DM {channel.get('id')}"


def choose_channel(channels, *, input_fn=input):
    """Print a numbered menu and return the channel the user selects."""
    for i, ch in enumerate(channels, start=1):
        print(f"  {i}. {channel_label(ch)}  (id={ch['id']})")
    while True:
        raw = input_fn(f"Select a chat [1-{len(channels)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(channels):
            return channels[int(raw) - 1]
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
    with _client() as client:
        try:
            channels = client.list_dm_channels()
        except Unauthorized:
            client.close()
            with DiscordClient(get_token(force_relogin=True)) as fresh:
                channels = fresh.list_dm_channels()
                return _run_sync(fresh, channels, args)
        return _run_sync(client, channels, args)


def _run_sync(client, channels, args):
    if args.channel:
        target = next((c for c in channels if c["id"] == args.channel), None)
        if target is None:
            target = {"id": args.channel, "type": 1, "recipients": []}
    else:
        if not channels:
            print("No DM or group channels found.")
            return
        target = choose_channel(channels)

    db = Database(args.db)
    try:
        count = Syncer(client, db).sync_channel(target)
        print(f"Synced {count} message(s) for {channel_label(target)} -> {args.db}")
    finally:
        db.close()


def build_parser():
    parser = argparse.ArgumentParser(prog="discord-scraper")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="log in via browser and store the token")
    sub.add_parser("list", help="list your DM and group channels")
    p_sync = sub.add_parser("sync", help="fetch/update a channel into the database")
    p_sync.add_argument("--channel", help="channel id (skip the interactive menu)")

    return parser


_COMMANDS = {"auth": cmd_auth, "list": cmd_list, "sync": cmd_sync}


def main(argv=None):
    args = build_parser().parse_args(argv)
    _COMMANDS[args.command](args)
