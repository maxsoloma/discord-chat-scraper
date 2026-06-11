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
