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

    def sync_channel(self, channel, on_progress=None):
        """Backfill if the channel is new, otherwise update.

        Returns the number of NEW messages fetched (the full history on a first
        backfill, or only the messages newer than the last sync on an update).
        Edit-window re-fetches of already-stored messages are not counted.

        ``on_progress``, if given, is called with the running message count after
        each fetched page so the caller can show live progress on long channels.
        """
        self._db.upsert_channel(channel)
        channel_id = channel["id"]
        newest = self._db.newest_message_id(channel_id)
        if newest is None:
            count = self._backfill(channel_id, on_progress=on_progress)
        else:
            count = self._update(channel_id, newest, on_progress=on_progress)
        self._db.set_sync_state(
            channel_id, self._db.newest_message_id(channel_id), _now_iso()
        )
        return count

    def _backfill(self, channel_id, *, on_progress=None):
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
            if on_progress is not None:
                on_progress(total)
            before = page[-1]["id"]  # newest-first array -> last element is oldest
            if len(page) < PAGE_LIMIT:
                break
        return total

    def _update(self, channel_id, newest_stored, *, on_progress=None):
        new_count = self._fetch_after(channel_id, newest_stored, on_progress=on_progress)
        # The edit window re-fetches recent messages to catch edits; those are
        # already-stored messages, not new ones, so they are NOT counted in the
        # reported total (otherwise "synced N" is inflated by up to edit_window).
        self._refresh_edit_window(channel_id)
        return new_count

    def _fetch_after(self, channel_id, after_id, *, on_progress=None):
        cursor = after_id
        total = 0
        while True:
            page = self._client.get_messages(
                channel_id, after=cursor, limit=PAGE_LIMIT
            )
            if not page:
                break
            self._db.upsert_messages(channel_id, page)
            total += len(page)
            if on_progress is not None:
                on_progress(total)
            cursor = page[0]["id"]  # newest-first array -> first element is newest
            if len(page) < PAGE_LIMIT:
                break
        return total

    def _refresh_edit_window(self, channel_id):
        """Re-fetch the newest `edit_window` messages and upsert, catching edits."""
        remaining = self._edit_window
        before = None
        total = 0
        while remaining > 0:
            limit = min(PAGE_LIMIT, remaining)
            page = self._client.get_messages(
                channel_id, before=before, limit=limit
            )
            if not page:
                break
            self._db.upsert_messages(channel_id, page)
            total += len(page)
            before = page[-1]["id"]
            remaining -= len(page)
            if len(page) < limit:
                break
        return total
