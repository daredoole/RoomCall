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
