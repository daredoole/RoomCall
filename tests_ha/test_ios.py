"""The native app advertises an OAuth callback without granting access itself."""

from homeassistant.setup import async_setup_component


async def test_ios_indieauth_metadata_is_public_but_commands_are_not(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "roomcall", {})
    client = await hass_client_no_auth()
    response = await client.get("/roomcall/ios")
    assert response.status == 200
    assert response.content_type == "text/html"
    assert response.headers["Cache-Control"] == "no-store"
    body = await response.text()
    assert '<link rel="redirect_uri" href="roomcall://auth-callback">' in body
    assert "token" not in body and "speakers" not in body
    assert (await client.post("/api/roomcall/command", json={"operation": "catalog"})).status == 401


def test_native_labels_and_format_arguments_keep_their_meaning():
    """Catch shifted translation rows even when generated resources are in sync."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components/roomcall"
    english = json.loads((root / "translations/en.json").read_text())["selector"]["communication"][
        "options"
    ]
    assert {
        key: english[key]
        for key in ("ios_send", "ios_task_done", "ios_someone_at", "ios_dnd_on", "ios_dnd_off")
    } == {
        "ios_send": "Send",
        "ios_task_done": "Marked done.",
        "ios_someone_at": "Someone is at the %@.",
        "ios_dnd_on": "Do Not Disturb is on.",
        "ios_dnd_off": "Do Not Disturb is off.",
    }
    for path in (root / "translations").glob("*.json"):
        translated = json.loads(path.read_text())["selector"]["communication"]["options"]
        for key, value in english.items():
            if key.startswith("ios_"):
                assert re.findall(r"%(?:@|d|s)", value) == re.findall(
                    r"%(?:@|d|s)", translated[key]
                ), (path.name, key)


async def test_public_ios_client_validates_when_private_server_ca_is_untrusted(
    hass, aioclient_mock
):
    """Exercise HA's actual redirect validator, including the failing private-CA route."""
    from pathlib import Path
    from unittest.mock import patch

    import aiohttp
    from homeassistant.components.auth.indieauth import verify_redirect_uri

    client_id = "https://daredoole.github.io/RoomCall/ios-client.html"
    callback = "roomcall://auth-callback"
    private_id = "https://ha.example.test/roomcall/ios"
    def read_metadata():
        return (Path(__file__).resolve().parents[1] / "docs/ios-client.html").read_text()

    body = await hass.async_add_executor_job(read_metadata)
    assert len(body.encode()) < 10240  # HA reads only the start of client metadata.
    aioclient_mock.get(private_id, exc=aiohttp.ClientSSLError(None, OSError("untrusted CA")))
    aioclient_mock.get(client_id, text=body)
    with patch(
        "homeassistant.components.auth.indieauth.aiohttp.ClientSession",
        side_effect=lambda: aioclient_mock.create_session(hass.loop),
    ):
        assert not await verify_redirect_uri(hass, private_id, callback)
        assert await verify_redirect_uri(hass, client_id, callback)
        assert not await verify_redirect_uri(hass, client_id, "roomcall://unexpected-callback")
