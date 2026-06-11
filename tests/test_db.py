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
