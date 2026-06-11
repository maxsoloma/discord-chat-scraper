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
