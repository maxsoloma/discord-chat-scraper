"""SQLite storage for Discord channels and messages."""

import json
import sqlite3
from datetime import datetime, timezone

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


class Database:
    """Thin SQLite wrapper for archived channels and messages."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

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
