# Discord Chat Scraper — Design

Date: 2026-06-11
Beads epic: `discord-chat-scraper-tss`
Status: Approved

## Goal

A Python tool that:
1. Authenticates the user's own Discord account by capturing the user token from a real browser login session.
2. Lets the user pick one of their DM / group-DM channels interactively and fetches the full message history into a SQLite database.
3. Supports incremental updates of an already-fetched channel (new messages plus a recent edit window).

## Scope & Non-Goals

In scope:
- User-account access via token (DMs, group DMs, and any server channel the user can see).
- Browser-assisted token capture (user logs in manually; script harvests the token from network traffic).
- Full backfill of a channel, incremental update, and edit detection in a recent window.
- SQLite storage with standard columns plus the full raw JSON of every message.

Out of scope (v1, YAGNI):
- Downloading attachment files (we store URL + metadata only).
- Detecting deletions of old messages (would require full re-sync).
- Reactions / embeds / mentions as first-class columns (they live inside `raw_json`).
- Official bot-token mode.
- Async/concurrent fetching (sequential is sufficient and simpler).

## Compliance note

Automating a Discord **user** account violates Discord's Terms of Service and can
result in account termination. This tool is intended for personal archival of
one's own conversations. The login itself is performed manually by the human in a
real browser (no credential automation, no captcha solving); the script only
reads the `Authorization` header the browser already sends. The risk is documented
and accepted by the user.

## Architecture

```
[cli] ──► [auth]  launch browser → user logs in → capture Authorization header → keyring
   │
   ├──► [api]  DiscordClient (httpx): REST calls to discord.com/api with the token
   │
   ├──► [sync] orchestration: backfill (full history) / update (new + edit window)
   │
   └──► [db]   SQLite: upsert messages + per-channel sync state
```

The browser (Playwright) is used **only** to obtain the token. All message
fetching and updates go through the fast REST client. The browser is re-launched
only when the stored token is rejected (HTTP 401).

## Modules

| Module | Responsibility | Depends on |
|--------|----------------|-----------|
| `cli.py` | argparse subcommands + interactive channel menu | sync, auth, db |
| `auth.py` | Playwright: open login window → intercept `Authorization` from a network request → store/retrieve token | keyring, playwright |
| `api.py` | `DiscordClient`: list DM/group channels, paginate messages, handle rate-limit / 401 / 403 | httpx |
| `db.py` | SQLite schema, message upsert, read/write sync state | sqlite3 (stdlib) |
| `sync.py` | Full backfill + incremental update + edit window | api, db |

The API surface is abstracted behind a small client interface so `sync.py` can be
unit-tested against a fake client without any network access.

## Data model (SQLite)

```sql
CREATE TABLE channels (
  id                      TEXT PRIMARY KEY,
  type                    INTEGER,
  name                    TEXT,            -- display name (group name or recipient)
  recipients_json         TEXT,            -- raw recipients array
  last_synced_message_id  TEXT,            -- newest message id seen on last sync
  last_synced_at          TEXT             -- ISO timestamp of last sync
);

CREATE TABLE messages (
  id                     TEXT PRIMARY KEY,
  channel_id             TEXT NOT NULL REFERENCES channels(id),
  author_id              TEXT,
  author_username        TEXT,
  author_global_name     TEXT,
  content                TEXT,
  type                   INTEGER,
  timestamp              TEXT,             -- ISO 8601 from Discord
  edited_timestamp       TEXT,             -- null if never edited
  referenced_message_id  TEXT,             -- reply target, if any
  raw_json               TEXT NOT NULL,    -- full message object, nothing lost
  fetched_at             TEXT
);
CREATE INDEX idx_messages_channel ON messages(channel_id);

CREATE TABLE attachments (
  id            TEXT PRIMARY KEY,
  message_id    TEXT NOT NULL REFERENCES messages(id),
  filename      TEXT,
  url           TEXT,
  proxy_url     TEXT,
  size          INTEGER,
  content_type  TEXT
);
CREATE INDEX idx_attachments_message ON attachments(message_id);
```

Message upsert uses `INSERT ... ON CONFLICT(id) DO UPDATE` so edits overwrite the
existing row (including `edited_timestamp` and `raw_json`). Multiple channels can
be archived into a single database file.

## Sync behavior

- **Backfill (channel not yet in DB):** page backwards with the `before` cursor,
  100 messages per request, until the history is exhausted.
- **Update (channel already in DB):**
  1. Fetch forward with `after = last_synced_message_id` to pull everything new.
  2. Re-fetch the most recent N = 200 messages and upsert them, catching edits via
     `edited_timestamp`.
- After either path, update `last_synced_message_id` (newest id seen) and
  `last_synced_at`.

`sync` decides automatically: channel absent → backfill; channel present → update.

## Discord API endpoints used

- `GET /users/@me/channels` — list the user's DM and group-DM channels.
- `GET /channels/{channel_id}/messages?limit=100&before=<id>` — paginate older.
- `GET /channels/{channel_id}/messages?limit=100&after=<id>` — paginate newer.

(Exact endpoint shapes, pagination semantics, and rate-limit headers will be
verified against current Discord API docs during implementation.)

## Error handling

| Condition | Handling |
|-----------|----------|
| HTTP 429 (rate limited) | Sleep for `Retry-After`, then retry the same request. |
| HTTP 401 (token invalid) | Discard stored token, re-launch browser for re-auth. |
| HTTP 403 (no access) | Warn and skip that channel. |
| Network / 5xx errors | Exponential backoff retry, bounded attempts. |

## CLI

```
python -m discord_scraper auth                 # log in, capture & store token
python -m discord_scraper list                 # show your DM/group channels
python -m discord_scraper sync                 # menu → pick channel → backfill or update (auto)
python -m discord_scraper sync --channel <id>  # skip the menu
  --db PATH                                     # database path (default ./discord_archive.db)
```

## Testing strategy (TDD)

- `test_db.py`: schema creation, message upsert idempotency, edit overwrite,
  sync-state read/write.
- `test_sync.py`: backfill pagination, incremental `after` cursor, edit-window
  upsert — driven by a **fake** API client (no network).
- `test_api.py`: rate-limit (429 + Retry-After), 401, 403 behavior using a mocked
  httpx transport (`respx`).
- `auth.py` (browser): integration/manual only; not unit-tested. Token capture is
  factored behind a single function so the rest of the system never touches the
  browser.

## Dependencies

- Runtime: `httpx`, `playwright`, `keyring`.
- Dev: `pytest`, `respx`.
- Python 3.11+.
- Interactive menu uses stdlib (`input`/numbered list) — no extra dependency.

## Decisions / defaults

1. Token storage: OS keyring (`keyring`), with a gitignored local file fallback.
2. Attachments: store URL + metadata only; do not download files.
3. Edit window: N = 200 messages (configurable).
4. Default database path: `./discord_archive.db`.
5. HTTP client: `httpx` (sync), sequential requests.
