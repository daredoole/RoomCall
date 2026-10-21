"""Assist intent: lets voice assistants and AI conversation agents make announcements.

Home Assistant offers every registered intent to LLM-based conversation agents as a tool, so
"tell the kitchen dinner is ready" works with any AI agent connected to Assist.
"""

from __future__ import annotations

from typing import ClassVar

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .const import DOMAIN, PRIORITY_NORMAL, PRIORITY_URGENT, SERVICE_BROADCAST
from .core import area_id_for

INTENT_ANNOUNCE = "RoomCallAnnounce"


@callback
def async_register(hass: HomeAssistant) -> None:
    intent.async_register(hass, AnnounceIntent())


class AnnounceIntent(intent.IntentHandler):
    intent_type = INTENT_ANNOUNCE
    description = (
        "Speak a message out loud on the home's speakers with the RoomCall intercom: in one "
        "room (area), or everywhere when no area is given. Use it for announcements such as "
        "'tell the kitchen dinner is ready' or 'announce that the car is here'. Set urgent "
        "only for important messages; it overrides Do Not Disturb and quiet hours."
    )
    slot_schema: ClassVar[dict] = {
        vol.Required("message"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("urgent"): cv.boolean,
    }
    platforms = None  # always available to agents

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        message = slots["message"]["value"]
        area = slots.get("area", {}).get("value")
        urgent = bool(slots.get("urgent", {}).get("value"))
        data: dict = {
            "message": message,
            "priority": PRIORITY_URGENT if urgent else PRIORITY_NORMAL,
        }
        if area:
            area_id = area_id_for(hass, area)
            if area_id is None:
                raise intent.IntentHandleError(f"There is no room called {area}")
            data["areas"] = [area_id]
        try:
            await hass.services.async_call(DOMAIN, SERVICE_BROADCAST, data, blocking=True)
        except HomeAssistantError as err:
            raise intent.IntentHandleError(str(err)) from err
        response = intent_obj.create_response()
        response.async_set_speech(f"Announced in {area}." if area else "Announced everywhere.")
        return response
