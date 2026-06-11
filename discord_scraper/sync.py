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
