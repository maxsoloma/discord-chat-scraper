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
            return msgs[-limit:] if limit else []
        return msgs[:limit]
