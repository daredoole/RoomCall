"""The card is registered as a dashboard resource so it never shows a configuration error."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.roomcall import frontend
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _resources(hass: HomeAssistant):
    resources = hass.data["lovelace"].resources
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    return resources


async def test_card_becomes_a_dashboard_resource(hass: HomeAssistant, setup_roomcall):
    assert await async_setup_component(hass, "lovelace", {})
    assert await frontend.async_register_card(hass) == "resource"
    items = (await _resources(hass)).async_items()
    assert [(i["type"], i["url"]) for i in items] == [("module", frontend.card_url())]
    # Running again (every start) neither duplicates nor changes it.
    assert await frontend.async_register_card(hass) == "resource"
    assert len((await _resources(hass)).async_items()) == 1


async def test_old_version_is_updated_and_duplicates_removed(hass: HomeAssistant, setup_roomcall):
    assert await async_setup_component(hass, "lovelace", {})
    resources = await _resources(hass)
    old = f"{frontend.CARD_PATH}?v=0.0.1"
    await resources.async_create_item({"res_type": "module", "url": old})
    await resources.async_create_item({"res_type": "module", "url": old})
    await resources.async_create_item({"res_type": "module", "url": "/hacsfiles/other.js"})
    await frontend.async_register_card(hass)
    urls = sorted(i["url"] for i in resources.async_items())
    assert urls == sorted([frontend.card_url(), "/hacsfiles/other.js"])


async def test_yaml_dashboards_fall_back_to_extra_js(hass: HomeAssistant, setup_roomcall):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"mode": "yaml"}})
    with patch("custom_components.roomcall.frontend.add_extra_js_url") as extra:
        assert await frontend.async_register_card(hass) == "extra_js"
    extra.assert_called_once_with(hass, frontend.card_url())
