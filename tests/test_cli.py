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
