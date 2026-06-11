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
