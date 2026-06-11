import httpx
import respx

from discord_scraper.api import DiscordClient

BASE = "https://discord.com/api/v10"


@respx.mock
def test_get_current_user_sends_raw_token():
    route = respx.get(f"{BASE}/users/@me").mock(
        return_value=httpx.Response(200, json={"id": "42", "username": "me"})
    )
    with DiscordClient("RAWTOKEN") as client:
        user = client.get_current_user()

    assert user == {"id": "42", "username": "me"}
    sent = route.calls[0].request
    # User token: raw value, NO "Bot "/"Bearer " prefix.
    assert sent.headers["authorization"] == "RAWTOKEN"


@respx.mock
def test_list_dm_channels_filters_to_dm_and_group():
    respx.get(f"{BASE}/users/@me/channels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "1", "type": 1, "recipients": [{"id": "9"}]},   # DM -> keep
                {"id": "2", "type": 3, "name": "group"},               # GROUP_DM -> keep
                {"id": "3", "type": 0, "name": "guild-text"},          # guild text -> drop
            ],
        )
    )
    with DiscordClient("T") as client:
        chans = client.list_dm_channels()

    assert [c["id"] for c in chans] == ["1", "2"]


import pytest

from discord_scraper.api import Unauthorized, Forbidden

MSGS = f"{BASE}/channels/123/messages"


@respx.mock
def test_get_messages_passes_params_and_returns_json():
    route = respx.get(MSGS).mock(
        return_value=httpx.Response(200, json=[{"id": "5"}, {"id": "4"}])
    )
    with DiscordClient("T") as client:
        out = client.get_messages("123", before="6", limit=100)

    assert out == [{"id": "5"}, {"id": "4"}]
    sent = route.calls[0].request
    assert sent.url.params["limit"] == "100"
    assert sent.url.params["before"] == "6"
    assert "after" not in sent.url.params


@respx.mock
def test_get_messages_retries_on_429_then_succeeds():
    calls = []  # record sleeps to prove we waited without actually sleeping

    route = respx.get(MSGS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.0"},
                           json={"message": "rate limited", "retry_after": 0.0}),
            httpx.Response(200, json=[{"id": "1"}]),
        ]
    )
    with DiscordClient("T", sleep=calls.append) as client:
        out = client.get_messages("123")

    assert out == [{"id": "1"}]
    assert route.call_count == 2
    assert calls == [0.0]  # slept once for Retry-After


@respx.mock
def test_get_messages_retries_on_500_then_succeeds():
    route = respx.get(MSGS).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=[{"id": "1"}]),
        ]
    )
    with DiscordClient("T", sleep=lambda s: None) as client:
        out = client.get_messages("123")

    assert out == [{"id": "1"}]
    assert route.call_count == 2


@respx.mock
def test_401_raises_unauthorized():
    respx.get(f"{BASE}/users/@me").mock(return_value=httpx.Response(401))
    with DiscordClient("BAD") as client:
        with pytest.raises(Unauthorized):
            client.get_current_user()


@respx.mock
def test_403_raises_forbidden():
    respx.get(MSGS).mock(return_value=httpx.Response(403))
    with DiscordClient("T") as client:
        with pytest.raises(Forbidden):
            client.get_messages("123")
