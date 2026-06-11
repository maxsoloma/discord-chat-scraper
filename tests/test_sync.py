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
        # 'written' is the count of NEW messages (4 and 5); the edit-window
        # re-fetch of already-stored messages is not counted.
        assert written == 2
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


def test_update_reports_new_count_not_edit_window(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(i) for i in range(1, 51)])  # 50 stored
        db.set_sync_state("100", "50", "2026-01-01T00:00:00+00:00")

        # Server has 1..52: only 51 and 52 are NEW. edit_window=200 re-fetches
        # all 52 to catch edits, but the reported count must reflect ONLY the 2
        # genuinely new messages, not the edit-window re-fetches.
        client = FakeDiscordClient({"100": [_m(i) for i in range(1, 53)]})
        written = Syncer(client, db, edit_window=200).sync_channel(
            {"id": "100", "type": 1, "recipients": []}
        )

        assert written == 2  # NOT 2 + 52 edit-window re-fetches
        total = db.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        assert total == 52
    finally:
        db.close()


def test_backfill_reports_progress_per_page(tmp_path):
    msgs = [_m(i) for i in range(1, 251)]  # 250 -> 3 pages (100, 100, 50)
    client = FakeDiscordClient({"100": msgs})
    db = Database(str(tmp_path / "a.db"))
    try:
        seen = []
        Syncer(client, db).sync_channel(
            {"id": "100", "type": 1, "recipients": []}, on_progress=seen.append
        )
        # progress reports the running total after each page
        assert seen == [100, 200, 250]
    finally:
        db.close()


def test_update_reports_progress_for_new_messages(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    try:
        db.upsert_channel({"id": "100", "type": 1, "recipients": []})
        db.upsert_messages("100", [_m(1)])
        db.set_sync_state("100", "1", "2026-01-01T00:00:00+00:00")
        client = FakeDiscordClient({"100": [_m(i) for i in range(1, 252)]})  # 250 new

        seen = []
        Syncer(client, db).sync_channel(
            {"id": "100", "type": 1, "recipients": []}, on_progress=seen.append
        )
        assert seen == [100, 200, 250]
    finally:
        db.close()
