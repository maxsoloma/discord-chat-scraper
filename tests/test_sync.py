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
