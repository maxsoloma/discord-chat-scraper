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
