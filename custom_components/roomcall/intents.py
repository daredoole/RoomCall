"""Assist intent with truthful service results; explicit LLM tools live in llm.py."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .communication import validate_targets
from .const import DOMAIN, PRIORITY_NORMAL, PRIORITY_URGENT, SERVICE_BROADCAST
from .core import area_id_for
from .errors import Problem
from .localization import text
from .schema import vol

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
                raise intent.IntentHandleError(text(hass, "no_targets", intent_obj.language))
            data["areas"] = [area_id]
        try:
            await validate_targets(
                hass,
                {**data, "everywhere": not bool(area)},
                intent_obj.context,
                "conversation" if intent_obj.platform == "conversation" else None,
            )
            result = await hass.services.async_call(
                DOMAIN,
                SERVICE_BROADCAST,
                data,
                blocking=True,
                return_response=True,
                context=intent_obj.context,
            )
        except (HomeAssistantError, Problem) as err:
            raise intent.IntentHandleError(str(err)) from err
        response = intent_obj.create_response()
        results = (result or {}).get("results", [])
        failed = [r["speaker"] for r in results if not r["ok"]]
        accepted = sum(r["ok"] for r in results)
        if failed:
            speech = text(
                hass,
                "partial" if accepted else "failed",
                intent_obj.language,
                accepted=accepted,
                speakers=", ".join(failed),
            )
        elif accepted:
            speech = text(hass, "accepted", intent_obj.language, count=accepted)
        else:
            speech = text(hass, "failed", intent_obj.language, speakers=area or "RoomCall")
        response.async_set_speech(speech)
        return response
