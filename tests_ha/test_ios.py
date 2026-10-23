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
