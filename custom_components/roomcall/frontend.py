"""Load the dashboard card the way Home Assistant expects: as a Lovelace resource.

Dashboards load their resources before drawing cards. A card injected with
``add_extra_js_url`` can arrive after the dashboard has drawn, which shows "Configuration
error" until the page is refreshed. In storage mode RoomCall therefore registers (and keeps
up to date) its own dashboard resource; in YAML mode it falls back to ``add_extra_js_url``.
"""

from __future__ import annotations

import logging

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.core import HomeAssistant

from .const import CARD_FILENAME, URL_STATIC, VERSION

_LOGGER = logging.getLogger(__name__)

CARD_PATH = f"{URL_STATIC}/{CARD_FILENAME}"


def card_url() -> str:
    return f"{CARD_PATH}?v={VERSION}"


async def async_register_card(hass: HomeAssistant) -> str:
    """Make sure dashboards load the card; returns how ("resource" or "extra_js")."""
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if (
        lovelace is None
        or getattr(lovelace, "resource_mode", None) != "storage"
        or not hasattr(resources, "async_create_item")
    ):
        add_extra_js_url(hass, card_url())
        return "extra_js"
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    mine = [r for r in resources.async_items() if r["url"].split("?", 1)[0] == CARD_PATH]
    if not mine:
        await resources.async_create_item({"res_type": "module", "url": card_url()})
        _LOGGER.info("RoomCall card added to dashboard resources")
    else:
        first, *duplicates = mine
        if first["url"] != card_url():
            await resources.async_update_item(
                first["id"], {"res_type": "module", "url": card_url()}
            )
        for duplicate in duplicates:
            await resources.async_delete_item(duplicate["id"])
    return "resource"
