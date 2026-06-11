# Discord Chat Scraper

Archive your own Discord DMs and group chats into a SQLite database, with
incremental updates.

> ⚠️ **Terms of Service warning.** This tool automates a Discord *user* account,
> which violates Discord's Terms of Service and can get your account banned. Use
> it only for personal archival of your own conversations, at your own risk. You
> log in yourself in a real browser window; the tool only reads the token your
> browser already sends.

## How it works

1. **Auth** — a real Chromium window opens at the Discord login page. You log in
   yourself (including MFA/captcha). The tool captures your user token from the
   `Authorization` header of the first authenticated API request, then stores it
   in your OS keyring. The browser is used *only* for this step.
2. **Fetch** — all message fetching goes through Discord's REST API directly
   (fast, no browser). You pick a DM or group chat from an interactive menu.
3. **Store** — messages land in SQLite. The first sync of a channel is a full
   backfill; later syncs fetch new messages and re-check the most recent 200 for
   edits.

## Quick start (`run.sh`)

The launcher sets everything up on first run (virtualenv, dependencies, and the
Chromium browser), then forwards your arguments to the CLI:

```bash
./run.sh auth                       # log in via browser, store the token
./run.sh list                       # list your DM / group channels
./run.sh sync                       # interactive: pick a chat, backfill/update
./run.sh sync --channel <id>        # skip the menu
./run.sh --db my.db sync            # custom database path
```

The first `./run.sh ...` downloads Chromium (~one-time). Override the interpreter
with `PYTHON=python3.12 ./run.sh ...`. If you only authenticate via the
`DISCORD_TOKEN` env var and never use `auth`, skip the browser download with
`SKIP_BROWSER_INSTALL=1 ./run.sh ...`.

## Install (manual)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

## Usage

```bash
# 1. Log in (opens a browser; complete login + MFA yourself), store the token:
.venv/bin/python -m discord_scraper auth

# 2. List your DM / group channels:
.venv/bin/python -m discord_scraper list

# 3. Fetch (first run = full backfill) or update a chat into SQLite:
.venv/bin/python -m discord_scraper sync                 # interactive menu
.venv/bin/python -m discord_scraper sync --channel <id>  # by id
.venv/bin/python -m discord_scraper --db my.db sync      # custom database path
```

Re-running `sync` on an already-archived channel fetches new messages and
re-checks the most recent 200 messages for edits.

If your stored token has expired, `sync` automatically reopens the browser to
re-authenticate. You can also force a fresh login any time with
`python -m discord_scraper auth`.

The token can also be supplied via the `DISCORD_TOKEN` environment variable
(used as a fallback when no OS keyring backend is available, e.g. on a headless
server).

## Data

One SQLite file (`discord_archive.db` by default) with `channels`, `messages`,
and `attachments` tables. Each message keeps both parsed columns and its full
raw JSON, so no data is lost. Attachment files are **not** downloaded — only
their URLs and metadata are stored.

## Development

```bash
.venv/bin/pytest          # run the test suite
```

Architecture: `auth` (browser token capture + keyring) → `api`
(`DiscordClient`, a sync httpx REST client) → `db` (`Database`, SQLite) →
`sync` (`Syncer`, backfill + incremental + edit window) → `cli` (argparse +
menu). The `Syncer` is tested against a fake client, so the core logic needs no
network. See `docs/superpowers/` for the design spec and implementation plan.
```
