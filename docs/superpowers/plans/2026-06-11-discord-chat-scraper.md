# Discord Chat Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python CLI that captures the user's Discord token via a real browser login, fetches DM/group-DM message history into SQLite, and supports incremental updates.

**Architecture:** A thin layered design — `auth` (Playwright token capture + keyring storage), `api` (sync httpx REST client for Discord), `db` (SQLite upsert + per-channel sync state), `sync` (backfill + incremental orchestration over an injectable client), `cli` (argparse + interactive menu). The browser is used only to obtain the token; all fetching is plain REST. `sync` depends on an injectable client interface so its logic is unit-tested against a fake — no network.

**Tech Stack:** Python 3.11+, httpx (sync), Playwright (Chromium, sync API), keyring, sqlite3 (stdlib); pytest + respx for tests.

**Spec:** `docs/superpowers/specs/2026-06-11-discord-chat-scraper-design.md`
**Beads epic:** `discord-chat-scraper-tss`

---

## Verified API facts driving this plan

These were verified against current docs (Context7 + Discord developer docs):

- **Discord base URL:** `https://discord.com/api/v10`.
- **User token auth:** header `Authorization: <raw token>` — **no** `Bot `/`Bearer ` prefix.
- **List DMs:** `GET /users/@me/channels` → array of channel objects. Keep `type == 1` (DM) and `type == 3` (GROUP_DM). Fields: `id`, `type`, `name`, `recipients[]`, `last_message_id`.
- **Messages:** `GET /channels/{id}/messages?limit=100&before=<id>|after=<id>` — always returned **newest-first**; `before`/`after`/`around` are mutually exclusive; `limit` max 100.
  - Backfill: pass `before=<id of last (oldest) element of previous page>` until a page has `< limit` items.
  - Incremental: pass `after=<newest stored id>`; response is still newest-first, so continue with `after=<id of first (newest) element of previous page>` until `< limit`.
- **Message fields:** `id`, `channel_id`, `author{id,username,global_name}`, `content`, `type`, `timestamp`, `edited_timestamp` (null if never edited), `message_reference{message_id}` (absent on non-replies), `attachments[]`.
- **Attachment fields:** `id`, `filename`, `url`, `proxy_url`, `size`, `content_type` (optional).
- **429:** body `{message, retry_after (float seconds), global}`; header `Retry-After`. httpx `response.headers.get('retry-after')` returns a **string** → `float()`. Check `status_code` **before** `raise_for_status()`.
- **httpx transport `retries=N` covers connection errors only**, not 5xx → 5xx needs a manual retry loop.
- **Playwright:** `request.headers` is a **property** (lowercased keys); `request.header_value(name)` is a **method** (case-insensitive). Use `context.on("request", handler)` (SPA-safe). Filter on URL prefix **and** presence of the `authorization` header (Discord makes tokenless `/api/` calls before login). Install is two steps: `pip install playwright` **then** `playwright install chromium`.
- **keyring:** `get_password` returns `None` if absent (no raise); `delete_password` raises `PasswordDeleteError` if absent; headless envs raise `NoKeyringError`.
- **respx:** `@respx.mock`; `respx.get(url).mock(return_value=httpx.Response(...))`; `side_effect=[...]` for sequences; with a `base_url` router, declare relative paths and query strings are ignored unless `params=` is set.

## File structure

```
pyproject.toml                 # metadata, deps, console script
.gitignore                     # (append) db, token file, browser profile, venv, pycache
README.md                      # usage + ToS warning
discord_scraper/
  __init__.py
  __main__.py                  # python -m discord_scraper -> cli.main()
  db.py                        # Database: schema, upsert, sync state
  api.py                       # DiscordClient + Unauthorized/Forbidden
  sync.py                      # Syncer: backfill + update
  auth.py                      # token storage (keyring) + browser capture
  cli.py                       # argparse, menu, command wiring
tests/
  fakes.py                     # FakeDiscordClient used by sync tests
  test_db.py
  test_api.py
  test_sync.py
  test_auth.py
  test_cli.py
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `discord_scraper/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "discord-chat-scraper"
version = "0.1.0"
description = "Archive Discord DMs/group chats to SQLite via browser-captured user token"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "playwright>=1.40",
    "keyring>=24",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "respx>=0.21",
]

[project.scripts]
discord-scraper = "discord_scraper.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["discord_scraper"]
```

- [ ] **Step 2: Create the package marker**

`discord_scraper/__init__.py`:
```python
"""Discord chat scraper: capture token via browser, archive DMs/groups to SQLite."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Append to `.gitignore`**

Add these lines to the existing `.gitignore`:
```gitignore
# Discord chat scraper
__pycache__/
*.pyc
.venv/
discord_archive.db
discord_archive.db-*
.token
discord-profile/
```

- [ ] **Step 4: Create venv and install (editable + browser)**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```
Expected: installs succeed; `playwright install chromium` downloads the browser.

- [ ] **Step 5: Verify the test harness runs (0 tests is fine)**

Run: `.venv/bin/pytest -q`
Expected: exit 0, "no tests ran" (or collected 0 items). Confirms `pytest` + editable import work.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml discord_scraper/__init__.py .gitignore
git commit -m "chore: scaffold discord_scraper package and tooling"
```

---

## Task 2: Database schema and connection

**Files:**
- Create: `discord_scraper/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:
```python
import sqlite3

from discord_scraper.db import Database


def _tables(db):
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {r["name"] for r in rows}


def test_opening_database_creates_schema(tmp_path):
    db = Database(str(tmp_path / "archive.db"))
    try:
        assert {"channels", "messages", "attachments"} <= _tables(db)
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_opening_database_creates_schema -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_scraper.db'`.

- [ ] **Step 3: Write minimal implementation**

`discord_scraper/db.py`:
```python
"""SQLite storage for Discord channels and messages."""

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id                     TEXT PRIMARY KEY,
    type                   INTEGER,
    name                   TEXT,
    recipients_json        TEXT,
    last_synced_message_id TEXT,
    last_synced_at         TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id                    TEXT PRIMARY KEY,
    channel_id            TEXT NOT NULL,
    author_id             TEXT,
    author_username       TEXT,
    author_global_name    TEXT,
    content               TEXT,
    type                  INTEGER,
    timestamp             TEXT,
    edited_timestamp      TEXT,
    referenced_message_id TEXT,
    raw_json              TEXT NOT NULL,
    fetched_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id);

CREATE TABLE IF NOT EXISTS attachments (
    id           TEXT PRIMARY KEY,
    message_id   TEXT NOT NULL,
    filename     TEXT,
    url          TEXT,
    proxy_url    TEXT,
    size         INTEGER,
    content_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
"""


class Database:
    """Thin SQLite wrapper for archived channels and messages."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/db.py tests/test_db.py
git commit -m "feat(db): create SQLite schema on open"
```

---

## Task 3: Message upsert (idempotent, edit-overwriting, with attachments)

**Files:**
- Modify: `discord_scraper/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:
```python
def _msg(mid, content="hi", edited=None, attachments=None, author=None):
    return {
        "id": mid,
        "channel_id": "100",
        "author": author or {"id": "7", "username": "ann", "global_name": "Ann"},
        "content": content,
        "type": 0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "edited_timestamp": edited,
        "message_reference": None,
        "attachments": attachments or [],
    }


def test_upsert_messages_inserts_rows(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        n = db.upsert_messages("100", [_msg("1"), _msg("2")])
        assert n == 2
        rows = db.conn.execute("SELECT id, content FROM messages ORDER BY id").fetchall()
        assert [(r["id"], r["content"]) for r in rows] == [("1", "hi"), ("2", "hi")]
    finally:
        db.close()


def test_upsert_messages_is_idempotent(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_messages("100", [_msg("1")])
        db.upsert_messages("100", [_msg("1")])
        count = db.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        assert count == 1
    finally:
        db.close()


def test_upsert_messages_overwrites_edited(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_messages("100", [_msg("1", content="before")])
        db.upsert_messages(
            "100", [_msg("1", content="after", edited="2026-01-02T00:00:00+00:00")]
        )
        row = db.conn.execute(
            "SELECT content, edited_timestamp FROM messages WHERE id='1'"
        ).fetchone()
        assert row["content"] == "after"
        assert row["edited_timestamp"] == "2026-01-02T00:00:00+00:00"
    finally:
        db.close()


def test_upsert_messages_replaces_attachments(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        att = [{"id": "a1", "filename": "f.png", "url": "u", "proxy_url": "p",
                "size": 10, "content_type": "image/png"}]
        db.upsert_messages("100", [_msg("1", attachments=att)])
        # Re-upsert with no attachments -> old attachment row must be gone.
        db.upsert_messages("100", [_msg("1", attachments=[])])
        count = db.conn.execute("SELECT COUNT(*) AS c FROM attachments").fetchone()["c"]
        assert count == 0
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: the four new tests FAIL with `AttributeError: 'Database' object has no attribute 'upsert_messages'`.

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `discord_scraper/db.py`:
```python
import json
import sqlite3
from datetime import datetime, timezone
```

Add a module-level helper and the methods on `Database`:
```python
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


_MESSAGE_UPSERT = """
INSERT INTO messages (
    id, channel_id, author_id, author_username, author_global_name,
    content, type, timestamp, edited_timestamp, referenced_message_id,
    raw_json, fetched_at
) VALUES (
    :id, :channel_id, :author_id, :author_username, :author_global_name,
    :content, :type, :timestamp, :edited_timestamp, :referenced_message_id,
    :raw_json, :fetched_at
)
ON CONFLICT(id) DO UPDATE SET
    content               = excluded.content,
    type                  = excluded.type,
    edited_timestamp      = excluded.edited_timestamp,
    referenced_message_id = excluded.referenced_message_id,
    raw_json              = excluded.raw_json,
    fetched_at            = excluded.fetched_at
"""
```

Add methods inside the `Database` class:
```python
    def upsert_messages(self, channel_id, messages):
        """Insert or update messages (and their attachments). Returns the count."""
        now = _now_iso()
        cur = self.conn.cursor()
        for m in messages:
            author = m.get("author") or {}
            ref = m.get("message_reference") or {}
            cur.execute(
                _MESSAGE_UPSERT,
                {
                    "id": m["id"],
                    "channel_id": channel_id,
                    "author_id": author.get("id"),
                    "author_username": author.get("username"),
                    "author_global_name": author.get("global_name"),
                    "content": m.get("content"),
                    "type": m.get("type"),
                    "timestamp": m.get("timestamp"),
                    "edited_timestamp": m.get("edited_timestamp"),
                    "referenced_message_id": ref.get("message_id"),
                    "raw_json": json.dumps(m, ensure_ascii=False),
                    "fetched_at": now,
                },
            )
            cur.execute("DELETE FROM attachments WHERE message_id = ?", (m["id"],))
            for a in m.get("attachments") or []:
                cur.execute(
                    "INSERT OR REPLACE INTO attachments "
                    "(id, message_id, filename, url, proxy_url, size, content_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        a.get("id"),
                        m["id"],
                        a.get("filename"),
                        a.get("url"),
                        a.get("proxy_url"),
                        a.get("size"),
                        a.get("content_type"),
                    ),
                )
        self.conn.commit()
        return len(messages)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/db.py tests/test_db.py
git commit -m "feat(db): upsert messages with attachment replacement"
```

---

## Task 4: Channel upsert, sync state, newest message id

**Files:**
- Modify: `discord_scraper/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:
```python
def test_upsert_channel_then_update_keeps_sync_state(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "name": None,
                           "recipients": [{"id": "7", "username": "ann"}]})
        db.set_sync_state("100", "55", "2026-01-01T00:00:00+00:00")
        # Re-upsert the channel (e.g. name changed) must NOT wipe sync state.
        db.upsert_channel({"id": "100", "type": 1, "name": "renamed", "recipients": []})
        last_id, last_at = db.get_sync_state("100")
        assert last_id == "55"
        assert last_at == "2026-01-01T00:00:00+00:00"
        row = db.conn.execute("SELECT name FROM channels WHERE id='100'").fetchone()
        assert row["name"] == "renamed"
    finally:
        db.close()


def test_get_sync_state_unknown_channel_is_none(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        assert db.get_sync_state("nope") is None
    finally:
        db.close()


def test_newest_message_id_uses_numeric_order(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        # "100" < "99" lexically but 100 > 99 numerically; snowflakes are numeric.
        db.upsert_messages("100", [_msg("99"), _msg("100")])
        assert db.newest_message_id("100") == "100"
        assert db.newest_message_id("empty") is None
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: new tests FAIL with `AttributeError` for `upsert_channel`/`set_sync_state`/`get_sync_state`/`newest_message_id`.

- [ ] **Step 3: Write minimal implementation**

Add methods inside the `Database` class:
```python
    def upsert_channel(self, channel):
        """Insert or update a channel row WITHOUT touching its sync state."""
        self.conn.execute(
            """
            INSERT INTO channels (id, type, name, recipients_json)
            VALUES (:id, :type, :name, :recipients_json)
            ON CONFLICT(id) DO UPDATE SET
                type            = excluded.type,
                name            = excluded.name,
                recipients_json = excluded.recipients_json
            """,
            {
                "id": channel["id"],
                "type": channel.get("type"),
                "name": channel.get("name"),
                "recipients_json": json.dumps(
                    channel.get("recipients") or [], ensure_ascii=False
                ),
            },
        )
        self.conn.commit()

    def set_sync_state(self, channel_id, last_message_id, last_synced_at):
        self.conn.execute(
            "UPDATE channels SET last_synced_message_id = ?, last_synced_at = ? "
            "WHERE id = ?",
            (last_message_id, last_synced_at, channel_id),
        )
        self.conn.commit()

    def get_sync_state(self, channel_id):
        row = self.conn.execute(
            "SELECT last_synced_message_id, last_synced_at FROM channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return (row["last_synced_message_id"], row["last_synced_at"])

    def newest_message_id(self, channel_id):
        row = self.conn.execute(
            "SELECT id FROM messages WHERE channel_id = ? "
            "ORDER BY CAST(id AS INTEGER) DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        return row["id"] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/db.py tests/test_db.py
git commit -m "feat(db): channel upsert, sync state, numeric newest-id lookup"
```

---

## Task 5: DiscordClient — construction, auth header, current user

**Files:**
- Create: `discord_scraper/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

`tests/test_api.py`:
```python
import httpx
import respx

from discord_scraper.api import DiscordClient

BASE = "https://discord.com/api/v10"


@respx.mock
def test_get_current_user_sends_raw_token():
    route = respx.get(f"{BASE}/users/@me").mock(
        return_value=httpx.Response(200, json={"id": "42", "username": "me"})
    )
    with DiscordClient("RAWTOKEN") as client:
        user = client.get_current_user()

    assert user == {"id": "42", "username": "me"}
    sent = route.calls[0].request
    # User token: raw value, NO "Bot "/"Bearer " prefix.
    assert sent.headers["authorization"] == "RAWTOKEN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_scraper.api'`.

- [ ] **Step 3: Write minimal implementation**

`discord_scraper/api.py`:
```python
"""Synchronous Discord REST client (user token) with rate-limit handling."""

import time

import httpx

BASE_URL = "https://discord.com/api/v10"
USER_AGENT = "discord-chat-scraper/0.1 (+https://github.com/local/discord-chat-scraper)"


class Unauthorized(Exception):
    """Raised on HTTP 401 — the token is missing or invalid."""


class Forbidden(Exception):
    """Raised on HTTP 403 — no access to the requested resource."""


class DiscordClient:
    def __init__(
        self,
        token,
        *,
        base_url=BASE_URL,
        timeout=30.0,
        max_retries=4,
        base_delay=0.5,
        sleep=time.sleep,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": token, "User-Agent": USER_AGENT},
            timeout=timeout,
        )
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._client.close()

    def get_current_user(self):
        return self._request("GET", "/users/@me").json()
```

Add the `_request` retry loop (used by every call):
```python
    def _request(self, method, path, *, params=None):
        resp = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.request(method, path, params=params)
            except httpx.RequestError:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._base_delay * (2 ** attempt))
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                try:
                    wait = float(retry_after) if retry_after is not None else \
                        self._base_delay * (2 ** attempt)
                except ValueError:
                    wait = self._base_delay * (2 ** attempt)
                self._sleep(wait)
                continue

            if 500 <= resp.status_code < 600 and attempt < self._max_retries:
                self._sleep(self._base_delay * (2 ** attempt))
                continue

            if resp.status_code == 401:
                raise Unauthorized()
            if resp.status_code == 403:
                raise Forbidden(path)

            resp.raise_for_status()
            return resp

        # Retries exhausted (e.g. persistent 429/5xx): surface the last status.
        resp.raise_for_status()
        return resp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/api.py tests/test_api.py
git commit -m "feat(api): DiscordClient with raw user-token auth and request retry loop"
```

---

## Task 6: DiscordClient — list DM/group channels

**Files:**
- Modify: `discord_scraper/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:
```python
@respx.mock
def test_list_dm_channels_filters_to_dm_and_group():
    respx.get(f"{BASE}/users/@me/channels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "1", "type": 1, "recipients": [{"id": "9"}]},   # DM -> keep
                {"id": "2", "type": 3, "name": "group"},               # GROUP_DM -> keep
                {"id": "3", "type": 0, "name": "guild-text"},          # guild text -> drop
            ],
        )
    )
    with DiscordClient("T") as client:
        chans = client.list_dm_channels()

    assert [c["id"] for c in chans] == ["1", "2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::test_list_dm_channels_filters_to_dm_and_group -v`
Expected: FAIL with `AttributeError: 'DiscordClient' object has no attribute 'list_dm_channels'`.

- [ ] **Step 3: Write minimal implementation**

Add to the `DiscordClient` class:
```python
    DM = 1
    GROUP_DM = 3

    def list_dm_channels(self):
        channels = self._request("GET", "/users/@me/channels").json()
        return [c for c in channels if c.get("type") in (self.DM, self.GROUP_DM)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/api.py tests/test_api.py
git commit -m "feat(api): list DM and group-DM channels"
```

---

## Task 7: DiscordClient — get_messages, 429 retry, 5xx retry, 401/403

**Files:**
- Modify: `discord_scraper/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:
```python
import pytest

from discord_scraper.api import Unauthorized, Forbidden

MSGS = f"{BASE}/channels/123/messages"


@respx.mock
def test_get_messages_passes_params_and_returns_json():
    route = respx.get(MSGS).mock(
        return_value=httpx.Response(200, json=[{"id": "5"}, {"id": "4"}])
    )
    with DiscordClient("T") as client:
        out = client.get_messages("123", before="6", limit=100)

    assert out == [{"id": "5"}, {"id": "4"}]
    sent = route.calls[0].request
    assert sent.url.params["limit"] == "100"
    assert sent.url.params["before"] == "6"
    assert "after" not in sent.url.params


@respx.mock
def test_get_messages_retries_on_429_then_succeeds():
    calls = []  # record sleeps to prove we waited without actually sleeping

    route = respx.get(MSGS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.0"},
                           json={"message": "rate limited", "retry_after": 0.0}),
            httpx.Response(200, json=[{"id": "1"}]),
        ]
    )
    with DiscordClient("T", sleep=calls.append) as client:
        out = client.get_messages("123")

    assert out == [{"id": "1"}]
    assert route.call_count == 2
    assert calls == [0.0]  # slept once for Retry-After


@respx.mock
def test_get_messages_retries_on_500_then_succeeds():
    route = respx.get(MSGS).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=[{"id": "1"}]),
        ]
    )
    with DiscordClient("T", sleep=lambda s: None) as client:
        out = client.get_messages("123")

    assert out == [{"id": "1"}]
    assert route.call_count == 2


@respx.mock
def test_401_raises_unauthorized():
    respx.get(f"{BASE}/users/@me").mock(return_value=httpx.Response(401))
    with DiscordClient("BAD") as client:
        with pytest.raises(Unauthorized):
            client.get_current_user()


@respx.mock
def test_403_raises_forbidden():
    respx.get(MSGS).mock(return_value=httpx.Response(403))
    with DiscordClient("T") as client:
        with pytest.raises(Forbidden):
            client.get_messages("123")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: the new tests FAIL with `AttributeError: ... 'get_messages'` (and the import line is fine since `Unauthorized`/`Forbidden` already exist from Task 5).

- [ ] **Step 3: Write minimal implementation**

Add to the `DiscordClient` class:
```python
    def get_messages(self, channel_id, *, before=None, after=None, limit=100):
        params = {"limit": limit}
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        return self._request(
            "GET", f"/channels/{channel_id}/messages", params=params
        ).json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/api.py tests/test_api.py
git commit -m "feat(api): get_messages with pagination params; verify 429/5xx/401/403"
```

---

## Task 8: FakeDiscordClient for sync tests

**Files:**
- Create: `tests/fakes.py`
- Test: `tests/test_sync.py` (first test exercises the fake)

- [ ] **Step 1: Write the failing test**

`tests/test_sync.py`:
```python
from tests.fakes import FakeDiscordClient


def _m(mid):
    return {"id": str(mid), "channel_id": "100",
            "author": {"id": "7", "username": "ann", "global_name": "Ann"},
            "content": f"msg {mid}", "type": 0,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "edited_timestamp": None, "attachments": []}


def test_fake_returns_newest_first_and_honors_cursors():
    client = FakeDiscordClient({"100": [_m(1), _m(2), _m(3), _m(4), _m(5)]})

    # newest-first, limited
    assert [m["id"] for m in client.get_messages("100", limit=2)] == ["5", "4"]
    # before -> older than cursor, still newest-first
    assert [m["id"] for m in client.get_messages("100", before="3", limit=10)] == ["2", "1"]
    # after -> newer than cursor, still newest-first
    assert [m["id"] for m in client.get_messages("100", after="3", limit=10)] == ["5", "4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.fakes'` (or import error).

- [ ] **Step 3: Write minimal implementation**

`tests/fakes.py`:
```python
"""In-memory Discord client that mimics the real pagination semantics."""


class FakeDiscordClient:
    """Mimics DiscordClient.get_messages: newest-first, before/after cursors, limit."""

    def __init__(self, messages_by_channel=None, channels=None):
        # messages_by_channel: {channel_id: [message dicts in any order]}
        self._messages = {k: list(v) for k, v in (messages_by_channel or {}).items()}
        self._channels = list(channels or [])

    def set_messages(self, channel_id, messages):
        self._messages[channel_id] = list(messages)

    def list_dm_channels(self):
        return list(self._channels)

    def get_messages(self, channel_id, *, before=None, after=None, limit=100):
        msgs = self._messages.get(channel_id, [])
        if after is not None:
            msgs = [m for m in msgs if int(m["id"]) > int(after)]
        if before is not None:
            msgs = [m for m in msgs if int(m["id"]) < int(before)]
        msgs = sorted(msgs, key=lambda m: int(m["id"]), reverse=True)  # newest-first
        if after is not None:
            # Discord `after`: the `limit` messages CLOSEST to the cursor (the oldest
            # among those newer than it), still returned newest-first within the page.
            # (Using msgs[:limit] here is WRONG — it returns the globally-newest window,
            # which makes after-pagination terminate after one page and drop messages.)
            return msgs[-limit:] if limit else []
        return msgs[:limit]
```

> **Implementation note (correction discovered during execution):** an earlier draft of this
> fake returned `msgs[:limit]` for the `after` branch too. That does not match Discord's
> `after` semantics (which return the messages *immediately above* the cursor), so
> `_fetch_after`'s cursor (`page[0]["id"]`, the newest) would jump to the channel's newest id
> and the next page would be empty — silently dropping messages. The `after`-branch slice
> must be `msgs[-limit:]`. The production `_fetch_after` in Task 10 is correct as written.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fakes.py tests/test_sync.py
git commit -m "test(sync): add FakeDiscordClient mimicking pagination semantics"
```

---

## Task 9: Syncer — full backfill

**Files:**
- Create: `discord_scraper/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync.py`:
```python
from discord_scraper.db import Database
from discord_scraper.sync import Syncer


def test_backfill_fetches_all_messages_paginated(tmp_path):
    msgs = [_m(i) for i in range(1, 251)]  # 250 messages -> 3 pages at limit 100
    client = FakeDiscordClient({"100": msgs})
    db = Database(str(tmp_path / "a.db"))
    try:
        syncer = Syncer(client, db, edit_window=200)
        count = syncer.sync_channel({"id": "100", "type": 1, "recipients": []})

        assert count == 250
        total = db.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        assert total == 250
        assert db.newest_message_id("100") == "250"
        last_id, last_at = db.get_sync_state("100")
        assert last_id == "250"
        assert last_at is not None
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sync.py::test_backfill_fetches_all_messages_paginated -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_scraper.sync'`.

- [ ] **Step 3: Write minimal implementation**

`discord_scraper/sync.py`:
```python
"""Orchestrates fetching a channel's history into the database."""

from datetime import datetime, timezone

PAGE_LIMIT = 100


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class Syncer:
    def __init__(self, client, db, *, edit_window=200):
        self._client = client
        self._db = db
        self._edit_window = edit_window

    def sync_channel(self, channel):
        """Backfill if the channel is new, otherwise update. Returns rows written."""
        self._db.upsert_channel(channel)
        channel_id = channel["id"]
        newest = self._db.newest_message_id(channel_id)
        if newest is None:
            count = self._backfill(channel_id)
        else:
            count = self._update(channel_id, newest)
        self._db.set_sync_state(
            channel_id, self._db.newest_message_id(channel_id), _now_iso()
        )
        return count

    def _backfill(self, channel_id):
        before = None
        total = 0
        while True:
            page = self._client.get_messages(
                channel_id, before=before, limit=PAGE_LIMIT
            )
            if not page:
                break
            self._db.upsert_messages(channel_id, page)
            total += len(page)
            before = page[-1]["id"]  # newest-first array -> last element is oldest
            if len(page) < PAGE_LIMIT:
                break
        return total
```

> Note: `_update` is added in Task 10; `sync_channel` already references it, so this
> test only exercises the backfill branch (the channel is new/empty).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: the backfill test PASSes. (Other sync tests don't exist yet.)

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/sync.py tests/test_sync.py
git commit -m "feat(sync): full backfill with before-cursor pagination"
```

---

## Task 10: Syncer — incremental fetch of new messages

**Files:**
- Modify: `discord_scraper/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync.py`:
```python
def test_update_fetches_only_new_messages(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        # Pre-load the DB with messages 1..3 (newest stored = 3).
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(1), _m(2), _m(3)])
        db.set_sync_state("100", "3", "2026-01-01T00:00:00+00:00")

        # The server now has 1..5; update must add only 4 and 5.
        client = FakeDiscordClient({"100": [_m(i) for i in range(1, 6)]})
        syncer = Syncer(client, db, edit_window=200)
        written = syncer.sync_channel({"id": "100", "type": 1, "recipients": []})

        total = db.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        assert total == 5
        assert db.newest_message_id("100") == "5"
        # 'written' counts what the update fetched (>= the 2 new ones).
        assert written >= 2
    finally:
        db.close()


def test_update_with_more_than_one_page_of_new(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(1)])
        db.set_sync_state("100", "1", "2026-01-01T00:00:00+00:00")

        # 1 stored, server has 1..301 -> 300 new across 3 pages.
        client = FakeDiscordClient({"100": [_m(i) for i in range(1, 302)]})
        Syncer(client, db, edit_window=200).sync_channel(
            {"id": "100", "type": 1, "recipients": []}
        )

        total = db.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        assert total == 301
        assert db.newest_message_id("100") == "301"
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: new tests FAIL with `AttributeError: 'Syncer' object has no attribute '_update'`.

- [ ] **Step 3: Write minimal implementation**

Add to the `Syncer` class:
```python
    def _update(self, channel_id, newest_stored):
        total = self._fetch_after(channel_id, newest_stored)
        total += self._refresh_edit_window(channel_id)
        return total

    def _fetch_after(self, channel_id, after_id):
        cursor = after_id
        total = 0
        while True:
            page = self._client.get_messages(
                channel_id, after=cursor, limit=PAGE_LIMIT
            )
            if not page:
                break
            self._db.upsert_messages(channel_id, page)
            total += len(page)
            cursor = page[0]["id"]  # newest-first array -> first element is newest
            if len(page) < PAGE_LIMIT:
                break
        return total
```

Add a temporary stub so the edit-window call resolves (real body lands in Task 11):
```python
    def _refresh_edit_window(self, channel_id):
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/sync.py tests/test_sync.py
git commit -m "feat(sync): incremental fetch of new messages via after-cursor"
```

---

## Task 11: Syncer — edit window (catch edits to recent messages)

**Files:**
- Modify: `discord_scraper/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync.py`:
```python
def test_update_edit_window_overwrites_recent_edit(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(1), _m(2), _m(3)])
        db.set_sync_state("100", "3", "2026-01-01T00:00:00+00:00")

        # No NEW messages, but message 2 was edited on the server.
        edited2 = _m(2)
        edited2["content"] = "EDITED"
        edited2["edited_timestamp"] = "2026-02-02T00:00:00+00:00"
        client = FakeDiscordClient({"100": [_m(1), edited2, _m(3)]})

        Syncer(client, db, edit_window=200).sync_channel(
            {"id": "100", "type": 1, "recipients": []}
        )

        row = db.conn.execute("SELECT content FROM messages WHERE id='2'").fetchone()
        assert row["content"] == "EDITED"
    finally:
        db.close()


def test_edit_window_respects_configured_size(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(i) for i in range(1, 51)])
        db.set_sync_state("100", "50", "2026-01-01T00:00:00+00:00")
        client = FakeDiscordClient({"100": [_m(i) for i in range(1, 51)]})

        # edit_window=10 means only the newest 10 are re-fetched for edits.
        syncer = Syncer(client, db, edit_window=10)
        refreshed = syncer._refresh_edit_window("100")
        assert refreshed == 10
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: `test_edit_window_respects_configured_size` FAILs (stub returns 0); the overwrite test also fails (stub doesn't re-fetch).

- [ ] **Step 3: Replace the stub with the real implementation**

Replace `_refresh_edit_window` in `discord_scraper/sync.py`:
```python
    def _refresh_edit_window(self, channel_id):
        """Re-fetch the newest `edit_window` messages and upsert, catching edits."""
        remaining = self._edit_window
        before = None
        total = 0
        while remaining > 0:
            limit = min(PAGE_LIMIT, remaining)
            page = self._client.get_messages(
                channel_id, before=before, limit=limit
            )
            if not page:
                break
            self._db.upsert_messages(channel_id, page)
            total += len(page)
            before = page[-1]["id"]
            remaining -= len(page)
            if len(page) < limit:
                break
        return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sync.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/sync.py tests/test_sync.py
git commit -m "feat(sync): edit window re-fetches recent messages to catch edits"
```

---

## Task 12: Auth — token storage (keyring) with env fallback

**Files:**
- Create: `discord_scraper/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_auth.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_scraper.auth'`.

- [ ] **Step 3: Write minimal implementation (storage only; browser capture in Step 6)**

`discord_scraper/auth.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_scraper/auth.py tests/test_auth.py
git commit -m "feat(auth): keyring token storage with env-var fallback"
```

- [ ] **Step 6: Add browser capture (no unit test — integration code)**

Append to `discord_scraper/auth.py`:
```python
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
```

- [ ] **Step 7: Re-run the auth tests (capture code must not break imports)**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: all PASS (browser code is import-guarded inside the function).

- [ ] **Step 8: Commit**

```bash
git add discord_scraper/auth.py
git commit -m "feat(auth): browser token capture via Playwright request interception"
```

---

## Task 13: CLI — channel labels, argparse, command wiring

**Files:**
- Create: `discord_scraper/cli.py`
- Create: `discord_scraper/__main__.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests (pure helpers)**

`tests/test_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_scraper.cli'`.

- [ ] **Step 3: Write minimal implementation**

`discord_scraper/cli.py`:
```python
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
```

Add the command functions and `main`:
```python
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
```

`discord_scraper/__main__.py`:
```python
from .cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify the full suite passes**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Verify the CLI is wired (no network)**

Run: `.venv/bin/python -m discord_scraper --help`
Expected: usage text listing `auth`, `list`, `sync`.

- [ ] **Step 7: Commit**

```bash
git add discord_scraper/cli.py discord_scraper/__main__.py tests/test_cli.py
git commit -m "feat(cli): auth/list/sync commands with interactive channel menu"
```

---

## Task 14: README and end-to-end verification

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Discord Chat Scraper

Archive your own Discord DMs and group chats into a SQLite database, with
incremental updates.

> ⚠️ **Terms of Service warning.** This tool automates a Discord *user* account,
> which violates Discord's Terms of Service and can get your account banned. Use
> it only for personal archival of your own conversations, at your own risk. You
> log in yourself in a real browser window; the tool only reads the token your
> browser already sends.

## Install

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

## Data

One SQLite file (`discord_archive.db` by default) with `channels`, `messages`,
and `attachments` tables. Each message keeps both parsed columns and its full
raw JSON. Attachment files are not downloaded — only their URLs/metadata.
```

- [ ] **Step 2: Run the full test suite one more time**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS, exit 0.

- [ ] **Step 3: Manual end-to-end smoke (requires a real account; optional but recommended)**

```bash
.venv/bin/python -m discord_scraper auth     # log in
.venv/bin/python -m discord_scraper list     # confirm channels print
.venv/bin/python -m discord_scraper sync     # pick a small chat; confirm a count prints
.venv/bin/sqlite3 discord_archive.db "SELECT COUNT(*) FROM messages;"
```
Expected: token captured, channels listed, a message count printed, DB row count matches.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: usage README with ToS warning"
```

---

## Self-review checklist (completed during planning)

- **Spec coverage:** auth via browser capture (Task 12) ✓; interactive channel selection (Task 13) ✓; fetch full history → SQLite (Tasks 2–4, 9) ✓; incremental update + edit window (Tasks 10–11) ✓; standard columns + raw JSON (Task 3) ✓; attachments URL/meta only (Task 3) ✓; rate-limit/401/403 handling (Tasks 5, 7) ✓; defaults — keyring+env, N=200, default db path, httpx sync (Tasks 5, 11, 12, 13) ✓.
- **Placeholder scan:** the only deliberate stub (`_refresh_edit_window` in Task 10) is explicitly replaced in Task 11; no TBD/TODO/"handle errors" placeholders remain.
- **Type/name consistency:** `Database`, `DiscordClient`, `Syncer`, `get_token`, `channel_label`, `choose_channel`, `get_messages(before=,after=,limit=)`, `sync_channel`, `upsert_messages`, `newest_message_id` are used identically across tasks and tests.
```
