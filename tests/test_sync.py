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
