"""Synchronous Discord REST client (user token) with rate-limit handling."""

import time

import httpx

BASE_URL = "https://discord.com/api/v10"
USER_AGENT = "discord-chat-scraper/0.1 (+https://github.com/local/discord-chat-scraper)"


class Unauthorized(Exception):
    """Raised on HTTP 401 — the token is missing or invalid."""


class Forbidden(Exception):
    """Raised on HTTP 403 — no access to the requested resource."""


class DiscordClient:
    def __init__(
        self,
        token,
        *,
        base_url=BASE_URL,
        timeout=30.0,
        max_retries=4,
        base_delay=0.5,
        sleep=time.sleep,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": token, "User-Agent": USER_AGENT},
            timeout=timeout,
        )
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._client.close()

    def get_current_user(self):
        return self._request("GET", "/users/@me").json()

    def _request(self, method, path, *, params=None):
        resp = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.request(method, path, params=params)
            except httpx.RequestError:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._base_delay * (2 ** attempt))
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                try:
                    wait = float(retry_after) if retry_after is not None else \
                        self._base_delay * (2 ** attempt)
                except ValueError:
                    wait = self._base_delay * (2 ** attempt)
                self._sleep(wait)
                continue

            if 500 <= resp.status_code < 600 and attempt < self._max_retries:
                self._sleep(self._base_delay * (2 ** attempt))
                continue

            if resp.status_code == 401:
                raise Unauthorized()
            if resp.status_code == 403:
                raise Forbidden(path)

            resp.raise_for_status()
            return resp

        # Retries exhausted (e.g. persistent 429/5xx): surface the last status.
        resp.raise_for_status()
        return resp
