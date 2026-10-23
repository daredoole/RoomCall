"""Answer the door from any room, phone or watch.

When the configured doorbell rings, RoomCall says who is at the door in the chosen rooms,
sends phones a notification with the camera snapshot and an Answer button, and shows a
banner on every RoomCall card. Answer opens the camera's live view in Home Assistant,
where two-way talk works when the camera supports it (for example through go2rtc).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_track_state_change_event

from .calls import CallError
from .const import ALL_ROOMS
from .core import speaker_area
from .errors import Problem
from .localization import text

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)
COOLDOWN = 10  # seconds; a held or bouncing button rings once
QUIET = ("unavailable", "unknown", "off")


def label(hass: HomeAssistant, data: RoomCallData) -> str:
    """Where the door is: the doorbell's area name, else its friendly name."""
    entity = data.doorbell_entity or ""
    area = speaker_area(hass, entity) or (
        speaker_area(hass, data.doorbell_camera) if data.doorbell_camera else None
    )
    if area and (found := ar.async_get(hass).async_get_area(area)):
        return found.name
    state = hass.states.get(entity)
    return state.name if state else entity


def info(hass: HomeAssistant, data: RoomCallData) -> dict[str, Any] | None:
    """What the card needs to show the doorbell banner."""
    if not data.doorbell_entity:
        return None
    return {
        "entity": data.doorbell_entity,
        "camera": data.doorbell_camera,
        "name": label(hass, data),
    }


@callback
def async_track(hass: HomeAssistant, data: RoomCallData) -> Callable[[], None]:
    """Ring on presses: a binary sensor turning on, or a new button or event state."""
    rung: float | None = None  # no press yet, whatever the clock reads

    @callback
    def changed(event: Event[EventStateChangedData]) -> None:
        nonlocal rung
        new, old = event.data["new_state"], event.data["old_state"]
        # An event entity is "unknown" until its first press; "unavailable" means it just
        # came back online, which is not a press.
        if new is None or new.state in QUIET or (old is not None and old.state == "unavailable"):
            return
        if old is None:  # the entity appearing (e.g. restored at startup) is not a press
            return
        if old.state == new.state:  # attribute-only or forced update
            return
        if rung is not None and monotonic() - rung < COOLDOWN:
            return
        rung = monotonic()
        hass.async_create_task(ring(hass, data))

    if not data.doorbell_entity:
        return lambda: None
    return async_track_state_change_event(hass, [data.doorbell_entity], changed)


async def ring(hass: HomeAssistant, data: RoomCallData) -> None:
    from .messages import send

    name = label(hass, data)
    message = text(hass, "doorbell_message", name=name)
    data.metrics.counters["doorbell_rings"] += 1
    payload: dict[str, Any] = {
        "tag": "roomcall_doorbell",
        "group": "roomcall",
        "push": {"interruption-level": "time-sensitive"},
    }
    if camera := data.doorbell_camera:
        # The companion apps open a camera's live view from an entityId URI.
        uri = f"entityId:{camera}"
        payload |= {
            "image": f"/api/camera_proxy/{camera}",
            "entity_id": camera,
            "url": uri,
            "clickAction": uri,
            "actions": [{"action": "URI", "title": text(hass, "answer_door"), "uri": uri}],
        }
    for entity_id in data.notify:
        service = entity_id.removeprefix("notify.")
        if hass.services.has_service("notify", service):
            await hass.services.async_call(
                "notify",
                service,
                {"title": name, "message": message, "data": payload},
                blocking=False,
            )
    # Phones and watches first: the room announcement can wait behind TTS or a live call.
    try:
        await send(
            hass,
            data,
            {"areas": data.doorbell_rooms or [ALL_ROOMS], "message": message},
            Context(),
            notify=False,
        )
    except (CallError, Problem) as err:  # phones were already told
        _LOGGER.warning("RoomCall could not announce the doorbell: %s", err)
